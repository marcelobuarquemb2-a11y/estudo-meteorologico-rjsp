#!/usr/bin/env python3
"""Match official forecast vintages and measured observations at each valid local hour."""
from datetime import datetime, timedelta
from decimal import Decimal
import json
import math

from normalize_collection import FIELDS, MODELS
from study_config import (
    FIRST_FULL_FRAME, HORIZON_HOURS, ROOT, SERIES_START, SLOT_HOURS, SLOT_TOLERANCE,
    STATIONS, TZ, direction_octant, local_time, within_slot,
)

MODEL_NAMES = (*MODELS, "Meteoblue package (AI/mLM unverified)")
DIRECTION_TOLERANCE_DEG = 30


def values_present(values):
    return sum(
        type(values.get(field)) in (int, float) and math.isfinite(values[field])
        for field in FIELDS
    )


def direction_comparison(forecast_deg, observed_deg):
    forecast_octant = direction_octant(forecast_deg)
    observed_octant = direction_octant(observed_deg)
    error = None
    hit = None
    if forecast_octant is not None and observed_octant is not None:
        delta = abs(Decimal(str(forecast_deg)) - Decimal(str(observed_deg)))
        error = min(delta, 360 - delta)
        hit = forecast_octant == observed_octant or error < DIRECTION_TOLERANCE_DEG
    return {
        "forecast_direction_octant": forecast_octant,
        "observed_direction_octant": observed_octant,
        "direction_error_deg": float(error) if error is not None else None,
        "direction_hit": hit,
    }


def read_archive(path, unreadable):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        unreadable.append({"file": str(path.relative_to(ROOT)), "error": type(exc).__name__})
        return {}


def forecast_archives(unreadable):
    by_issue = {}
    first_window = SERIES_START - SLOT_TOLERANCE
    start_day = first_window.date().isoformat()
    for path in (ROOT / "data").glob("????-??/????-??-??/*.json"):
        day = path.parent.name
        if day < start_day or (
            day == start_day and len(path.stem) == 6 and path.stem.isdigit()
            and path.stem < first_window.strftime("%H%M%S")
        ):
            continue
        report = read_archive(path, unreadable)
        if (
            not report.get("official_series_eligible")
            or report.get("official_series_start_local") != SERIES_START.isoformat()
            or not report.get("nominal_issue_at_local")
        ):
            continue
        issue = local_time(report["nominal_issue_at_local"])
        consulted = local_time(report["consulted_at"])
        if issue < SERIES_START or not within_slot(consulted, issue):
            continue
        source = str(path.relative_to(ROOT))
        present = report.get("collection_summary", {}).get("forecast_values_present", 0)
        rank = (-present, abs(consulted - issue), consulted, source)
        if issue not in by_issue or rank < by_issue[issue][0]:
            by_issue[issue] = (rank, consulted, source, report["station_results"])
    return by_issue


def measured_by_time(active_ids, unreadable):
    observed = {}
    for path in sorted((ROOT / "data" / "observations" / "reports").glob("*/*.json")):
        report = read_archive(path, unreadable)
        if not report.get("target_local"):
            continue
        valid = local_time(report["target_local"])
        if valid < SERIES_START or valid.hour not in SLOT_HOURS:
            continue
        source = str(path.relative_to(ROOT))
        measured = observed.setdefault(valid, {})
        acquired_at = local_time(report["acquired_at_brt"])
        for sid, station in report["stations"].items():
            if sid not in active_ids:
                continue
            values = station.get("observed")
            timestamp = station.get("observed_at_local", report["target_local"])
            if not timestamp or not values:
                continue
            observed_at = local_time(timestamp)
            if not within_slot(observed_at, valid) or observed_at > acquired_at:
                continue
            rank = (-values_present(values), abs(observed_at - valid), observed_at, -acquired_at.timestamp())
            if sid not in measured or rank < measured[sid][0]:
                measured[sid] = (rank, source, values, observed_at)
    return observed


def main():
    active = {s["id"]: s for s in STATIONS if s.get("enabled", True)}
    unreadable = []
    forecasts = forecast_archives(unreadable)
    observations = measured_by_time(active, unreadable)
    frames = []
    for valid, measured in sorted(observations.items()):
        horizons = [
            hours for hours in HORIZON_HOURS
            if valid - timedelta(hours=hours) >= SERIES_START
        ]
        rows = []
        for hours in horizons:
            issue = valid - timedelta(hours=hours)
            if issue not in forecasts:
                continue
            _, consulted, path, station_results = forecasts[issue]
            for sid, station in active.items():
                forecast = station_results.get(sid, {})
                if (forecast.get("lat"), forecast.get("lon")) != (station["lat"], station["lon"]):
                    continue
                _, observation_source, actual, observed_at = measured.get(sid, (None, None, None, None))
                for row in forecast.get("forecasts", []):
                    if row["model"] not in MODEL_NAMES or not within_slot(local_time(row["target_local"]), valid):
                        continue
                    timestamp = row.get("valid_at_local", row["target_local"])
                    if not timestamp:
                        continue
                    forecast_valid = local_time(timestamp)
                    if not within_slot(forecast_valid, valid):
                        continue
                    values = row["values"]
                    rows.append({
                        "station_id": sid,
                        "valid_local": valid.isoformat(),
                        "forecast_consulted_at_local": consulted.isoformat(),
                        "forecast_valid_at_local": forecast_valid.isoformat(),
                        "observed_at_local": observed_at.isoformat() if observed_at else None,
                        "model": row["model"],
                        "nominal_horizon_hours": hours,
                        "actual_lead_hours": round((forecast_valid - consulted).total_seconds() / 3600, 3),
                        "forecast_source": path,
                        "observation_source": observation_source,
                        "forecast": {key: values.get(key) for key in FIELDS},
                        "observed": actual,
                        **direction_comparison(values.get("direction_deg"), (actual or {}).get("direction_deg")),
                        "comparable": (
                            values_present(actual or {}) == len(FIELDS)
                            and values_present(values) == len(FIELDS)
                        ),
                    })
        matched = {
            (row["station_id"], row["model"], row["nominal_horizon_hours"])
            for row in rows if row["comparable"]
        }
        expected = len(active) * len(MODEL_NAMES) * len(HORIZON_HOURS)
        observation_file = min(measured.values(), default=((), None, None, None), key=lambda item: item[0])[1]
        frames.append({
            "observed_time": valid.isoformat(),
            "observation_file": observation_file,
            "rows": rows,
            "matched_rows": len(rows),
            "rows_with_real_observations": len(matched),
            "expected_rows_full_frame": expected,
            "complete": len(matched) == expected,
        })
    out = ROOT / "data" / "comparisons"
    out.mkdir(parents=True, exist_ok=True)
    (out / "status.json").write_text(json.dumps({
        "generated_at": datetime.now(TZ).isoformat(),
        "series_start_local": SERIES_START.isoformat(),
        "slot_tolerance_minutes": int(SLOT_TOLERANCE.total_seconds() / 60),
        "first_complete_nominal_15h_frame": FIRST_FULL_FRAME.isoformat(),
        "unreadable_archives": unreadable,
        "frames": frames,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Comparison frames:", len(frames))
    print("Complete frames:", sum(frame["complete"] for frame in frames))


if __name__ == "__main__":
    main()
