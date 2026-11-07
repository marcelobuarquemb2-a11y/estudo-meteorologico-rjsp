#!/usr/bin/env python3
"""Collect genuine surface observations at the study's scheduled local times."""
import hashlib
import json
import math
import os
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from study_config import ROOT, SERIES_START, SLOT_HOURS, STATIONS, TZ

KNOTS_PER_MPS = 1.9438444924
WIS2 = "https://wis2bra.inmet.gov.br/oapi/collections/urn:wmo:md:br-inmet:synop/items"
SMA = "https://sma.portosrio.gov.br/api/ma/estacao/sismowater"
ACTIVE = [s for s in STATIONS if s.get("enabled", True)]
INMET = {
    s["id"]: (s["name"], s["observation_source"]["wigos"], s["lat"], s["lon"])
    for s in ACTIVE if s["observation_source"]["provider"] == "inmet_wis2"
}
PORTOSRIO = {
    s["id"]: (
        s["observation_source"]["station_id"], s["name"].upper(),
        s["observation_source"]["instrument"], s["lat"], s["lon"],
    )
    for s in ACTIVE if s["observation_source"]["provider"] == "portosrio_sma"
}
WIS_FIELDS = {
    "direction_deg": ("wind_direction", "deg", 360, 10),
    "mean_kt": ("wind_speed", "m/s", 90, 10),
    "gust_kt": ("maximum_wind_gust_speed", "m/s", 100, 60),
    "precip_mm": ("total_precipitation_or_total_water_equivalent", "kg m-2", 500, 60),
}
SMA_FIELDS = {
    "direction_deg": ("WindDir", "graus, de onde o vento vem, norte geográfico", 360),
    "mean_kt": ("WindSpeed", "nós", 175),
    "gust_kt": ("WindGust", "nós", 195),
    "precip_mm": ("Precipt_5min", "mm", 500),
}
OBSERVATIONS = ROOT / "data" / "observations"
SCHEDULE_HOURS = {
    "20 13 * * *": 9, "20 14 * * *": 9,
    "20 16 * * *": 12, "20 17 * * *": 12,
    "20 19 * * *": 15, "20 20 * * *": 15,
    "20 22 * * *": 18, "20 23 * * *": 18,
}

def numeric(value, maximum):
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return value if 0 <= value <= maximum else None


def fetch_json(endpoint, params, raw_dir, filename):
    url = endpoint + "?" + urllib.parse.urlencode(params, safe=",:/")
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "RJSP-observations/1.0"}
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        body = response.read()
        status = response.status
    data = json.loads(body)
    path = raw_dir / filename
    if endpoint == WIS2:
        wigos = {station[1] for station in INMET.values()}
        archive = {
            "type": data["type"],
            "features": [
                feature for feature in data["features"]
                if feature["properties"].get("wigos_station_identifier") in wigos
            ]
        }
    else:
        station = data["estacao"]
        fields = {definition[0] for definition in SMA_FIELDS.values()} | {"Precipt_24hs_calculada"}
        channels = [
            {
                key: meta[key] for key in
                ("codg_tipo_dado", "nome_tipo_dado", "numg_estacao", "equipamento", "unidade_medida")
            }
            for meta in station["tipos_dado"] if meta["nome_tipo_dado"] in fields
        ]
        channel_ids = {str(meta["codg_tipo_dado"]) for meta in channels}
        public_station = {
            key: station[key] for key in
            ("numg_estacao", "nome_estacao", "codg_latitude", "codg_longitude")
        }
        public_station["tipos_dado"] = channels
        archive = {
            "estacao": public_station,
            "dados": [
                row for row in data["dados"] if str(row["data_type_id"]) in channel_ids
            ],
        }
    path.write_text(json.dumps(archive, ensure_ascii=False) + "\n", encoding="utf-8")
    evidence = {
        "url": url,
        "http_status": status,
        "raw_path": str(path.relative_to(ROOT)),
        "source_response_sha256": hashlib.sha256(body).hexdigest(),
        "archived_records_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    return data, evidence


def valid_position(actual, expected):
    return all(abs(float(a) - b) < 0.001 for a, b in zip(actual, expected, strict=True))


def as_utc(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def collect_wis2(targets, raw_dir):
    params = {
        "f": "json",
        "bbox": "-44.80,-23.30,-42.90,-22.86",
        "datetime": as_utc(min(targets) - timedelta(minutes=5))
        + "/" + as_utc(max(targets) + timedelta(minutes=5)),
        "limit": 5000,
    }
    payload, evidence = fetch_json(WIS2, params, raw_dir, "inmet-wis2.json")
    if payload.get("type") != "FeatureCollection" or any(
        link["rel"] == "next" for link in payload.get("links", [])
    ):
        raise ValueError("Unexpected or paginated WIS2 response")
    evidence["features"] = len(payload["features"])
    by_wigos = {config[1]: code for code, config in INMET.items()}
    readings = defaultdict(list)
    for feature in payload["features"]:
        p = feature["properties"]
        wigos = p.get("wigos_station_identifier")
        if wigos not in by_wigos:
            continue
        coords = feature["geometry"]["coordinates"]
        _, _, lat, lon = INMET[by_wigos[wigos]]
        if valid_position(coords[:2], (lon, lat)):
            readings[wigos, p.get("reportTime"), p.get("name")].append(p)

    result = {}
    for code, (label, wigos, lat, lon) in INMET.items():
        rows = []
        for target in targets:
            values = {}
            intervals = {}
            for field, (source, unit, maximum, minutes) in WIS_FIELDS.items():
                matches = readings[wigos, as_utc(target), source]
                value = None
                if len(matches) == 1:
                    match = matches[0]
                    observed_period = match.get("phenomenonTime")
                    expected_period = (
                        as_utc(target - timedelta(minutes=minutes)) + "/" + as_utc(target)
                    )
                    if observed_period == expected_period and match["units"] == unit:
                        value = numeric(match["value"], maximum)
                        intervals[field] = observed_period
                if value is not None and unit == "m/s":
                    value = round(value * KNOTS_PER_MPS, 3)
                values[field] = value
            rows.append({"target_brt": target.isoformat(), "observed": values, "intervals_utc": intervals})
        result[code] = {
            "name": label,
            "wigos": wigos,
            "lat": lat,
            "lon": lon,
            "provider": "INMET WIS2 SYNOP",
            "requests": [evidence],
            "records": rows,
        }
    return result


def sma_index(payload, config):
    station_id, name, instrument, lat, lon = config
    station = payload["estacao"]
    if (
        int(station["numg_estacao"]) != station_id
        or station["nome_estacao"] != name
        or not valid_position(
            (station["codg_latitude"], station["codg_longitude"]), (lat, lon)
        )
    ):
        raise ValueError("PortosRio returned a different station")
    source_by_id = {}
    for field, (name, unit, maximum) in SMA_FIELDS.items():
        metadata = [m for m in station["tipos_dado"] if m["nome_tipo_dado"] == name]
        if len(metadata) != 1:
            raise ValueError(f"Expected one PortosRio field named {name}")
        meta = metadata[0]
        if (
            meta["unidade_medida"]["nome_unidade_medida"] != unit
            or instrument not in meta["equipamento"]["nome_tipo_equipamento"]
            or int(meta["numg_estacao"]) != station_id
        ):
            raise ValueError(f"Unexpected PortosRio metadata for {name}")
        source_by_id[str(meta["codg_tipo_dado"])] = (field, maximum)
    if len(source_by_id) != len(SMA_FIELDS):
        raise ValueError("PortosRio channel identifiers are not distinct")

    readings = defaultdict(list)
    for row in payload["dados"]:
        source = source_by_id.get(str(row["data_type_id"]))
        if source is None:
            continue
        field, maximum = source
        timestamp = datetime.fromisoformat(row["date_time"])
        local = timestamp.astimezone(TZ) if timestamp.tzinfo else timestamp.replace(tzinfo=TZ)
        readings[field, local].append(numeric(row["data_value"], maximum))
    return readings


def sma_value(readings, field, timestamp):
    values = readings.get((field, timestamp), [])
    return values[0] if len(values) == 1 else None


def sma_record(readings, target):
    values = {
        field: sma_value(readings, field, target) for field in ("direction_deg", "mean_kt")
    }
    gaps = {}
    window = [target - timedelta(minutes=5 * offset) for offset in range(11, -1, -1)]
    for field, reducer in (("precip_mm", math.fsum), ("gust_kt", max)):
        pairs = [(t, sma_value(readings, field, t)) for t in window]
        missing = [t.isoformat() for t, value in pairs if value is None]
        values[field] = None if missing else round(reducer([v for _, v in pairs]), 3)
        gaps[field] = missing
    return {
        "target_brt": target.isoformat(),
        "observed": values,
        "missing_five_minute_slots": gaps,
    }


def collect_sma(code, config, targets, raw_dir):
    station_id, label, instrument, lat, lon = config
    result = {
        "name": label, "provider": "PortosRio SMA", "station_id": station_id,
        "instrument": instrument, "lat": lat, "lon": lon,
        "requests": [], "records": [], "errors": [],
    }
    for date in sorted({t.date() for t in targets}):
        on_date = [t for t in targets if t.date() == date]
        params = {
            "id": station_id,
            "date_start": (min(on_date) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M"),
            "date_end": (max(on_date) + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M"),
        }
        try:
            payload, evidence = fetch_json(SMA, params, raw_dir, f"portosrio-{code}-{date}.json")
            result["requests"].append(evidence)
            readings = sma_index(payload, config)
            result["records"].extend(sma_record(readings, t) for t in on_date)
        except Exception as exc:
            result["errors"].append({"date": date.isoformat(), "error": f"{type(exc).__name__}: {exc}"})
    return result


def observation_target(now, cron):
    override = os.environ.get("OBS_TARGET_BRT")
    if override:
        value = datetime.fromisoformat(override)
        return value.astimezone(TZ) if value.tzinfo else value.replace(tzinfo=TZ)
    if cron in SCHEDULE_HOURS:
        return now.replace(hour=SCHEDULE_HOURS[cron], minute=0, second=0, microsecond=0)
    available = now - timedelta(minutes=80)
    for hour in reversed(SLOT_HOURS):
        if hour <= available.hour:
            return available.replace(hour=hour, minute=0, second=0, microsecond=0)
    return (available - timedelta(days=1)).replace(
        hour=SLOT_HOURS[-1], minute=0, second=0, microsecond=0
    )


def main():
    now = datetime.now(TZ)
    cron = os.environ.get("GITHUB_EVENT_SCHEDULE", "")
    target = observation_target(now, cron)
    if target.hour not in SLOT_HOURS or target.minute or target.second or target.microsecond or target > now:
        raise ValueError("Expected a past nominal study hour")

    stamp = now.strftime("%H%M%S%f") + "_" + target.strftime("%Y%m%d_%H%M")
    raw_dir = OBSERVATIONS / "raw" / now.date().isoformat() / stamp
    raw_dir.mkdir(parents=True, exist_ok=True)
    errors = []
    try:
        readings = collect_wis2([target], raw_dir)
    except Exception as exc:
        errors.append({"provider": "INMET WIS2", "error": type(exc).__name__})
        readings = {}
    for sid, config in PORTOSRIO.items():
        readings[sid] = collect_sma(sid, config, [target], raw_dir)

    results = {}
    for station in ACTIVE:
        sid = station["id"]
        source = readings.get(sid, {})
        rows = source.get("records", [])
        row = rows[0] if rows else {}
        observed = row.get("observed", {})
        count = sum(value is not None for value in observed.values())
        if observed.get("precip_mm") is not None:
            observed["precip_mm"] = round(observed["precip_mm"], 3)
        status = (
            "observation_available" if count == len(WIS_FIELDS)
            else "partial_observation" if count else "observation_unavailable"
        )
        results[sid] = {
            "station_name": station["name"],
            "source": source.get("provider", station["observation_source"]["provider"]),
            "source_metadata": station["observation_source"],
            "lat": station["lat"],
            "lon": station["lon"],
            "status": status,
            "observed": observed if count else None,
            "requests": source.get("requests", []),
            "intervals_utc": row.get("intervals_utc"),
            "missing_five_minute_slots": row.get("missing_five_minute_slots"),
            "errors": source.get("errors", []),
        }

    report = {
        "acquired_at_brt": now.isoformat(),
        "target_local": target.isoformat(),
        "scheduled_cron": cron or None,
        "target_measurement_delayed_by_minutes": round((now - target).total_seconds() / 60, 1),
        "target_utc": target.astimezone(timezone.utc).isoformat(),
        "real_observations_only": True,
        "series_start": SERIES_START.isoformat(),
        "stations": results,
        "errors": errors,
    }
    out = OBSERVATIONS / "reports" / now.date().isoformat()
    out.mkdir(parents=True, exist_ok=True)
    path = out / (stamp + ".json")
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print("Observation report:", path.relative_to(ROOT))
    print("Complete stations:", sum(s["status"] == "observation_available" for s in results.values()))
    for sid, reading in results.items():
        print(sid, reading["status"])


if __name__ == "__main__":
    main()
