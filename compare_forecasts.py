#!/usr/bin/env python3
"""Match official forecast vintages and measured observations at each valid local hour."""
from datetime import datetime, timedelta
import json
import math

from normalize_collection import FIELDS, MODELS
from study_config import FIRST_FULL_FRAME, HORIZON_HOURS, ROOT, SERIES_START, SLOT_HOURS, STATIONS, TZ

MODEL_NAMES = (*MODELS, "Meteoblue package (AI/mLM unverified)")


def values_present(values):
    return sum(
        type(values.get(field)) in (int, float) and math.isfinite(values[field])
        for field in FIELDS
    )


def first_archives():
    by_issue = {}
    for path in (ROOT / "data").glob("????-??/????-??-??/*.json"):
        report = json.loads(path.read_text(encoding="utf-8"))
        if (
            not report.get("official_series_eligible")
            or report.get("official_series_start_local") != SERIES_START.isoformat()
            or not report.get("nominal_issue_at_local")
        ):
            continue
        issue = datetime.fromisoformat(report["nominal_issue_at_local"])
        consulted = datetime.fromisoformat(report["consulted_at"])
        if issue < SERIES_START or consulted < issue:
            continue
        if issue not in by_issue or consulted < by_issue[issue][0]:
            by_issue[issue] = (consulted, str(path.relative_to(ROOT)), report["station_results"])
    return by_issue


def measured_by_time(active_ids):
    observed = {}
    for path in sorted((ROOT / "data" / "observations" / "reports").glob("*/*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        valid = datetime.fromisoformat(report["target_local"])
        if valid < SERIES_START or valid.hour not in SLOT_HOURS:
            continue
        source = str(path.relative_to(ROOT))
        measured = observed.setdefault(valid, {})
        for sid, station in report["stations"].items():
            if sid not in active_ids:
                continue
            values = station.get("observed")
            rank = (values_present(values or {}), report["acquired_at_brt"])
            if sid not in measured or rank > measured[sid][0]:
                measured[sid] = (rank, source, values)
    return observed


def main():
    active = {s["id"]: s for s in STATIONS if s.get("enabled", True)}
    forecasts = first_archives()
    observations = measured_by_time(active)
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
            consulted, path, station_results = forecasts[issue]
            for sid, station in active.items():
                forecast = station_results.get(sid, {})
                if (forecast.get("lat"), forecast.get("lon")) != (station["lat"], station["lon"]):
                    continue
                _, observation_source, actual = measured.get(sid, (None, None, None))
                for row in forecast.get("forecasts", []):
                    if row["target_local"] != valid.strftime("%Y-%m-%dT%H:%M") or row["model"] not in MODEL_NAMES:
                        continue
                    values = row["values"]
                    rows.append({
                        "station_id": sid,
                        "valid_local": valid.isoformat(),
                        "model": row["model"],
                        "nominal_horizon_hours": hours,
                        "actual_lead_hours": round((valid - consulted).total_seconds() / 3600, 3),
                        "forecast_source": path,
                        "observation_source": observation_source,
                        "forecast": {key: values.get(key) for key in FIELDS},
                        "observed": actual,
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
        observation_file = max(measured.values(), default=((), None, None), key=lambda item: item[0])[1]
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
        "first_complete_nominal_15h_frame": FIRST_FULL_FRAME.isoformat(),
        "frames": frames,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Comparison frames:", len(frames))
    print("Complete frames:", sum(frame["complete"] for frame in frames))


if __name__ == "__main__":
    main()
