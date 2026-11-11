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
from collection_schedule import SCHEDULE_HOURS as WINDOW_SCHEDULE, needs_query

from study_config import (
    ROOT, SERIES_START, SLOT_HOURS, SLOT_TOLERANCE, STATIONS, TZ,
    direction_octant, local_time, within_slot,
)

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
    **WINDOW_SCHEDULE,
    "20 13 * * *": 9, "20 14 * * *": 9,
    "20 16 * * *": 12, "20 17 * * *": 12,
    "20 19 * * *": 15, "20 20 * * *": 15,
    "20 22 * * *": 18, "20 23 * * *": 18,
}

def archived_observations(target, now):
    """Reuse complete validated readings and record whether nominal was attempted."""
    saved = {}
    nominal_attempted = False
    configs = {station["id"]: station for station in ACTIVE}
    for path in sorted((OBSERVATIONS / "reports").glob("*/*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            if local_time(report["target_local"]) != target:
                continue
            acquired = local_time(report["acquired_at_brt"])
            if acquired > now:
                continue
            nominal_attempted |= acquired >= target
            for sid, record in report["stations"].items():
                config = configs.get(sid)
                if not config or (
                    record.get("source_metadata") != config["observation_source"]
                    or (record.get("lat"), record.get("lon")) != (config["lat"], config["lon"])
                ):
                    continue
                values = record.get("observed") or {}
                if not all(numeric(values.get(field), definition[2]) is not None for field, definition in SMA_FIELDS.items()):
                    continue
                measured = local_time(record["observed_at_local"])
                if measured > acquired or not within_slot(measured, target):
                    continue
                rank = (abs(measured - target), measured, -acquired.timestamp())
                if sid not in saved or rank < saved[sid][0]:
                    saved[sid] = (rank, {**record, "reused_from": str(path.relative_to(ROOT))})
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            print("Ignoring unusable observation artifact:", path.name)
    return {sid: record for sid, (_, record) in saved.items()}, nominal_attempted


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


def best_observation(records, target, available_at=None):
    def priority(record):
        actual = local_time(record["observed_at_local"])
        present = sum(value is not None for value in record["observed"].values())
        return -present, abs(actual - target), actual

    candidates = [
        record for record in records
        if available_at is None or local_time(record["observed_at_local"]) <= available_at
    ]
    best = min(candidates, key=priority) if candidates else {
        "observed_at_local": target.isoformat(),
        "observed": {field: None for field in WIS_FIELDS},
    }
    return {**best, "target_brt": target.isoformat()}


def collect_wis2(targets, raw_dir, available_at=None):
    available_at = available_at or datetime.now(TZ)
    params = {
        "f": "json",
        "bbox": "-44.80,-23.30,-42.90,-22.86",
        "datetime": as_utc(min(targets) - SLOT_TOLERANCE)
        + "/" + as_utc(min(max(targets) + SLOT_TOLERANCE, available_at)),
        "limit": 5000,
    }
    payload, evidence = fetch_json(WIS2, params, raw_dir, "inmet-wis2.json")
    if payload.get("type") != "FeatureCollection" or any(
        link["rel"] == "next" for link in payload.get("links", [])
    ):
        raise ValueError("Unexpected or paginated WIS2 response")
    evidence["features"] = len(payload["features"])
    by_wigos = {config[1]: code for code, config in INMET.items()}
    source_names = {definition[0] for definition in WIS_FIELDS.values()}
    readings = defaultdict(list)
    measured_times = defaultdict(set)
    for feature in payload["features"]:
        p = feature["properties"]
        wigos = p.get("wigos_station_identifier")
        source = p.get("name")
        if wigos not in by_wigos or source not in source_names:
            continue
        coords = feature["geometry"]["coordinates"]
        _, _, lat, lon = INMET[by_wigos[wigos]]
        if not valid_position(coords[:2], (lon, lat)):
            continue
        start, _, end = (p.get("phenomenonTime") or "").partition("/")
        try:
            actual = local_time(end)
            began = local_time(start)
        except ValueError:
            continue
        if actual > available_at:
            continue
        measured_times[wigos].add(actual)
        readings[wigos, actual, source].append((p, began))

    result = {}
    for code, (label, wigos, lat, lon) in INMET.items():
        rows = []
        for target in targets:
            candidates = {when for when in measured_times[wigos] if within_slot(when, target)}
            records = []
            for actual in candidates or {target}:
                values = {}
                intervals = {}
                for field, (source, unit, maximum, minutes) in WIS_FIELDS.items():
                    matches = readings.get((wigos, actual, source), [])
                    value = None
                    if len(matches) == 1:
                        match, began = matches[0]
                        if actual - began == timedelta(minutes=minutes) and match["units"] == unit:
                            value = numeric(match["value"], maximum)
                            intervals[field] = match["phenomenonTime"]
                    if value is not None and unit == "m/s":
                        value = round(value * KNOTS_PER_MPS, 3)
                    values[field] = value
                records.append({"observed_at_local": actual.isoformat(), "observed": values, "intervals_utc": intervals})
            rows.append(best_observation(records, target, available_at))
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
        local = local_time(row["date_time"])
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
        "observed_at_local": target.isoformat(),
        "observed": values,
        "missing_five_minute_slots": gaps,
    }


def collect_sma(code, config, targets, raw_dir, available_at=None):
    available_at = available_at or datetime.now(TZ)
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
            "date_start": (min(on_date) - timedelta(hours=1) - SLOT_TOLERANCE).strftime("%Y-%m-%d %H:%M"),
            "date_end": (min(max(on_date) + SLOT_TOLERANCE, available_at) + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M"),
        }
        try:
            payload, evidence = fetch_json(SMA, params, raw_dir, f"portosrio-{code}-{date}.json")
            result["requests"].append(evidence)
            readings = sma_index(payload, config)
            measured_times = {when for _, when in readings}
            for target in on_date:
                candidates = {when for when in measured_times if when <= available_at and within_slot(when, target)}
                records = (sma_record(readings, when) for when in candidates)
                result["records"].append(best_observation(records, target, available_at))
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
    nearby = [now.replace(hour=hour, minute=0, second=0, microsecond=0) for hour in SLOT_HOURS]
    in_window = [target for target in nearby if target - timedelta(minutes=20) <= now <= target + SLOT_TOLERANCE]
    if in_window:
        return min(in_window, key=lambda target: abs(now - target))
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
    if target.hour not in SLOT_HOURS or target.minute or target.second or target.microsecond or now < target - timedelta(minutes=20):
        raise ValueError("Expected a nominal study hour, at most 20 minutes ahead")
    previous, nominal_attempted = archived_observations(target, now)
    complete = len(previous) == len(ACTIVE)
    if cron in WINDOW_SCHEDULE and now > target + SLOT_TOLERANCE:
        if complete:
            print("Late observation attempt skipped; complete readings already archived")
            return
        raise SystemExit("Scheduled observation attempt missed the consultation window; delayed recovery remains enabled")
    if not needs_query(now, target, complete, nominal_attempted):
        print("Complete real observations already archived; no source queries needed")
        return
    refresh = now < target or not nominal_attempted
    pending = {station["id"] for station in ACTIVE if refresh or station["id"] not in previous}

    stamp = now.strftime("%H%M%S%f") + "_" + target.strftime("%Y%m%d_%H%M")
    raw_dir = OBSERVATIONS / "raw" / now.date().isoformat() / stamp
    raw_dir.mkdir(parents=True, exist_ok=True)
    errors = []
    readings = {}
    if pending.intersection(INMET):
        try:
            readings = collect_wis2([target], raw_dir, available_at=now)
        except Exception as exc:
            errors.append({"provider": "INMET WIS2", "error": type(exc).__name__})
    for sid, config in PORTOSRIO.items():
        if sid in pending:
            readings[sid] = collect_sma(sid, config, [target], raw_dir, available_at=now)

    results = {}
    for station in ACTIVE:
        sid = station["id"]
        if sid not in pending:
            results[sid] = previous[sid]
            continue
        source = readings.get(sid, {})
        rows = source.get("records", [])
        row = rows[0] if rows else {}
        observed = row.get("observed", {})
        count = sum(value is not None for value in observed.values())
        measured_at = row.get("observed_at_local") if count else None
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
            "observed_at_local": measured_at,
            "observation_offset_minutes": (
                round((local_time(measured_at) - target).total_seconds() / 60, 4) if measured_at else None
            ),
            "observed": observed if count else None,
            "direction_octant": direction_octant(observed.get("direction_deg")),
            "requests": source.get("requests", []),
            "intervals_utc": row.get("intervals_utc"),
            "missing_five_minute_slots": row.get("missing_five_minute_slots"),
            "errors": source.get("errors", []),
        }

    report = {
        "acquired_at_brt": now.isoformat(),
        "target_local": target.isoformat(),
        "scheduled_cron": cron or None,
        "nominal_refresh_attempt": now >= target,
        "acquisition_phase": "before_nominal" if now < target else (
            "delayed_recovery" if now > target + SLOT_TOLERANCE else "nominal_refresh" if refresh else "retry"
        ),
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

