"""Run a read-only acquisition test of the six selected surface stations."""

import hashlib
import json
import math
import os
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Sao_Paulo")
KNOTS_PER_MPS = 1.9438444924
WIS2 = "https://wis2bra.inmet.gov.br/oapi/collections/urn:wmo:md:br-inmet:synop/items"
SMA = "https://sma.portosrio.gov.br/api/ma/estacao/sismowater"
INMET = {
    "A619": ("Paraty", "0-76-0-3303807000000141", -23.2236111, -44.72694443),
    "A628": ("Angra dos Reis", "0-76-0-3300100000000555", -22.97555554, -44.30333333),
    "A602": ("Marambaia", "0-76-0-3304557000000024", -23.05027777, -43.59555555),
    "A652": ("Forte de Copacabana", "0-76-0-3304557000000187", -22.98833333, -43.19055555),
}
PORTOSRIO = {
    "ilha_rasa": (6, "ILHA RASA", "GMX551", -23.063400, -43.146167),
    "ilha_fiscal": (7, "ILHA FISCAL", "GMX600", -22.896683, -43.165933),
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
OUTPUT = Path("validation")
RAW = OUTPUT / "raw"
TARGETS = [
    datetime(2026, 11, day, hour, tzinfo=TZ)
    for day, hours in ((6, (9, 12, 15, 18)), (7, (9, 12)))
    for hour in hours
]


def numeric(value, maximum):
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return value if 0 <= value <= maximum else None


def fetch_json(endpoint, params, filename):
    url = endpoint + "?" + urllib.parse.urlencode(params, safe=",:/")
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "RJSP-observations-validation/1.0"}
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        body = response.read()
        status = response.status
    data = json.loads(body)
    path = RAW / filename
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
        "raw_path": str(path),
        "source_response_sha256": hashlib.sha256(body).hexdigest(),
        "archived_records_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    return data, evidence


def valid_position(actual, expected):
    return all(abs(float(a) - b) < 0.001 for a, b in zip(actual, expected, strict=True))


def as_utc(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def complete(record):
    return all(record["observed"][field] is not None for field in WIS_FIELDS)


def collect_wis2(targets):
    params = {
        "f": "json",
        "bbox": "-44.80,-23.30,-42.90,-22.86",
        "datetime": as_utc(min(targets) - timedelta(minutes=5))
        + "/" + as_utc(max(targets) + timedelta(minutes=5)),
        "limit": 5000,
    }
    payload, evidence = fetch_json(WIS2, params, "inmet-wis2.json")
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


def collect_sma(code, config, targets):
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
            payload, evidence = fetch_json(SMA, params, f"portosrio-{code}-{date}.json")
            result["requests"].append(evidence)
            readings = sma_index(payload, config)
            result["records"].extend(sma_record(readings, t) for t in on_date)
        except Exception as exc:
            result["errors"].append({"date": date.isoformat(), "error": f"{type(exc).__name__}: {exc}"})
    return result


def check_positive_rain(code, config):
    """Compare measured five-minute rain amounts with the daily counter on wet hours."""
    params = {"id": config[0], "date_start": "2026-11-04 00:00", "date_end": "2026-11-06 00:00"}
    payload, evidence = fetch_json(SMA, params, f"portosrio-{code}-rain-check.json")
    readings = sma_index(payload, config)
    station = payload["estacao"]
    metadata = [
        meta for meta in station["tipos_dado"]
        if meta["nome_tipo_dado"] == "Precipt_24hs_calculada"
        and meta["unidade_medida"]["nome_unidade_medida"] == "mm"
    ]
    if len(metadata) != 1:
        raise ValueError("Missing daily rainfall counter")
    channel = str(metadata[0]["codg_tipo_dado"])
    for row in payload["dados"]:
        if str(row["data_type_id"]) != channel:
            continue
        stamp = datetime.fromisoformat(row["date_time"])
        local = stamp.astimezone(TZ) if stamp.tzinfo else stamp.replace(tzinfo=TZ)
        readings["rain_daily", local].append(numeric(row["data_value"], 1000))

    wet_hours = []
    for day in (4, 5):
        for hour in range(1, 24):
            target = datetime(2026, 11, day, hour, tzinfo=TZ)
            rain = sma_record(readings, target)["observed"]["precip_mm"]
            before = sma_value(readings, "rain_daily", target - timedelta(hours=1))
            after = sma_value(readings, "rain_daily", target)
            if rain is None or rain <= 0 or before is None or after is None:
                continue
            change = round(after - before, 3)
            wet_hours.append({
                "target_brt": target.isoformat(),
                "sum_5min_mm": rain,
                "daily_counter_difference_mm": change,
                "matches_within_0_01_mm": abs(change - rain) <= 0.0100001,
            })
    return {"request": evidence, "wet_hours": wet_hours}


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    errors = []
    try:
        stations = collect_wis2(TARGETS)
    except Exception as exc:
        errors.append({"provider": "INMET WIS2", "error": f"{type(exc).__name__}: {exc}"})
        stations = {code: {"name": cfg[0], "records": []} for code, cfg in INMET.items()}
    for code, config in PORTOSRIO.items():
        stations[code] = collect_sma(code, config, TARGETS)
        try:
            stations[code]["positive_rain_check"] = check_positive_rain(code, config)
        except Exception as exc:
            stations[code]["positive_rain_check"] = {"error": f"{type(exc).__name__}: {exc}"}
    for station in stations.values():
        station["complete_slots"] = sum(complete(r) for r in station["records"])
        station["observed_values"] = sum(
            v is not None for r in station["records"] for v in r["observed"].values()
        )
    report = {
        "run_id": os.getenv("GITHUB_RUN_ID"),
        "collected_at_brt": datetime.now(TZ).isoformat(),
        "targets_brt": [t.isoformat() for t in TARGETS],
        "expected_stations": len(INMET) + len(PORTOSRIO),
        "expected_values": len(TARGETS) * (len(INMET) + len(PORTOSRIO)) * len(WIS_FIELDS),
        "received_values": sum(s["observed_values"] for s in stations.values()),
        "portosrio_intervals": {
            "wind_direction_and_mean": "native published averages at the exact local time",
            "gust": "maximum of 12 published five-minute gust records",
            "rain": "sum of 12 Precipt_5min readings in mm over (T-1h, T]",
        },
        "stations": stations,
        "errors": errors,
    }
    (OUTPUT / "report-seis-estacoes.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    for code, station in stations.items():
        print(f"{code} {station['complete_slots']}/{len(TARGETS)} complete slots")
    print(f"{report['received_values']}/{report['expected_values']} observed values")


if __name__ == "__main__":
    main()
