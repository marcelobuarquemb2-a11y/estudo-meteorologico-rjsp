#!/usr/bin/env python3
"""Independent D+1 actuals trial, without replacing or reusing same-day captures.

One batch requests all four prior-day slots per INMET WIS2 call or PortosRio
station. Comparison uses SAME-DAY archived readings as a frozen benchmark.
No original observation report is modified. Failure is a trial result, not an
invitation to weaken the study's +/-30-minute observation-time constraint.
"""
import argparse
import json
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

import collect_observations as obs
from study_config import ROOT, SLOT_HOURS, STATIONS, TZ, local_time, within_slot

FIELDS = ("direction_deg", "mean_kt", "gust_kt", "precip_mm")
CORE = ("direction_deg", "mean_kt", "gust_kt")
TRIAL = ROOT / "data" / "observations" / "dplus1_tests"
ACTIVE = [station for station in STATIONS if station.get("enabled", True)]
EXPECTED = len(ACTIVE) * len(SLOT_HOURS) * len(FIELDS)
assert EXPECTED == 96


def number(value):
    return type(value) in (float, int) and math.isfinite(value)


def targets_for(day):
    return [datetime.combine(day, time(h), tzinfo=TZ) for h in SLOT_HOURS]


def target_label(target):
    return target.isoformat()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_atomic(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def same_day_originals(day):
    """Only original acquisition-day reports; NEVER D+1 backfill or trial."""
    originals = {}
    folder = ROOT / "data" / "observations" / "reports" / day.isoformat()
    configs = {station["id"]: station for station in ACTIVE}
    for path in sorted(folder.glob("*.json")):
        try:
            report = read(path)
            target = local_time(report["target_local"])
            if target.date() != day or target.hour not in SLOT_HOURS or target.minute:
                continue
            acquired = local_time(report["acquired_at_brt"])
            if acquired.date() != day:
                continue
            for sid, record in report.get("stations", {}).items():
                config = configs.get(sid)
                if not config or record.get("source_metadata") != config["observation_source"]:
                    continue
                if (record.get("lat"), record.get("lon")) != (config["lat"], config["lon"]):
                    continue
                observed = record.get("observed") or {}
                count = sum(number(observed.get(field)) for field in FIELDS)
                if not count or not record.get("observed_at_local"):
                    continue
                measured = local_time(record["observed_at_local"])
                if not within_slot(measured, target) or measured > acquired:
                    continue
                rank = (-count, abs(measured - target), measured)
                key = (sid, target_label(target))
                if key not in originals or rank < originals[key][0]:
                    originals[key] = (rank, record, str(path.relative_to(ROOT)))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return {key: {"record": value[1], "path": value[2]} for key, value in originals.items()}


def compare(day, recovered, originals, provider_errors):
    """Strict conservative gate: no vanished or changed original observed field."""
    differences = []
    missing = []
    original_present = retrospective_present = matches = core_present = 0
    core_by_station = defaultdict(int)
    source_times_outside_window = []
    for target in targets_for(day):
        key_time = target_label(target)
        for station in ACTIVE:
            sid = station["id"]
            key = (sid, key_time)
            original = (originals.get(key) or {}).get("record", {})
            old = original.get("observed") or {}
            new = recovered.get(key, {})
            current = new.get("observed") or {}
            measured_at = new.get("observed_at_local")
            time_ok = bool(measured_at) and within_slot(local_time(measured_at), target)
            if not time_ok and any(number(v) for v in current.values()):
                source_times_outside_window.append({"station": sid, "target": key_time, "measured": measured_at})
            for field in FIELDS:
                before, after = old.get(field), current.get(field)
                original_present += int(number(before))
                retrospective_present += int(number(after) and time_ok)
                if field in CORE and number(after) and time_ok:
                    core_present += 1
                    core_by_station[sid] += 1
                if number(before):
                    if not number(after) or not time_ok:
                        missing.append({"station": sid, "target": key_time, "field": field, "original": before, "retrospective": after})
                    elif abs(before - after) > 0.002:
                        differences.append({"station": sid, "target": key_time, "field": field, "original": before, "retrospective": after})
                    else:
                        matches += 1
    passed = (
        not provider_errors and not missing and not differences
        and not source_times_outside_window
        and original_present >= 48
        and core_present >= 60
        and all(core_by_station[s["id"]] >= 9 for s in ACTIVE)
    )
    return {
        "passed": bool(passed), "expected": EXPECTED, "original_values_present": original_present,
        "dplus1_values_present": retrospective_present, "core_values_present": core_present,
        "core_by_station": dict(core_by_station), "original_values_matched": matches,
        "original_values_missing_in_dplus1": missing,
        "original_values_changed_in_dplus1": differences,
        "retrospective_measurement_times_invalid": source_times_outside_window,
        "provider_errors": provider_errors,
        "gate_rules": "No dropped or changed original values (<=0.002 tolerance); no provider errors; >=48 original values; >=60/72 D+1 direction/wind/gust; >=9/12 core per station; all measurement times within +/-30 min",
    }


def collect(day, now, fetch_wis2=None, fetch_sma=None):
    fetch_wis2 = fetch_wis2 or obs.collect_wis2
    fetch_sma = fetch_sma or obs.collect_sma
    targets = targets_for(day)
    output = TRIAL / day.isoformat()
    raw = output / "raw" / now.strftime("%Y%m%dT%H%M%S%f")
    original = same_day_originals(day)
    recovered = {}
    errors = []
    wis = {}
    try:
        wis = fetch_wis2(targets, raw, available_at=now)
    except Exception as exc:
        errors.append({"provider": "INMET WIS2", "error": type(exc).__name__ + ": " + str(exc)[:200]})
    for sid in obs.INMET:
        rows = wis.get(sid, {}).get("records") or []
        for target, row in zip(targets, rows):
            recovered[(sid, target_label(target))] = row
    for sid, config in obs.PORTOSRIO.items():
        try:
            station = fetch_sma(sid, config, targets, raw, available_at=now)
            for target, row in zip(targets, station.get("records") or []):
                recovered[(sid, target_label(target))] = row
            for error in station.get("errors", []):
                errors.append({"provider": "PortosRio", "station": sid, "error": error})
        except Exception as exc:
            errors.append({"provider": "PortosRio", "station": sid, "error": type(exc).__name__ + ": " + str(exc)[:200]})
    assessment = compare(day, recovered, original, errors)
    assessment.update({
        "record_type": "independent_dplus1_observation_trial",
        "reference_day": day.isoformat(),
        "retrieved_at_brt": now.isoformat(),
        "independent_provider_calls": True,
        "originals_reused_as_source": False,
        "raw_path": str(raw.relative_to(ROOT)),
        "recorded_values": [
            {"station": sid, "target_local": target, "observed_at_local": row.get("observed_at_local"),
             "observed": row.get("observed")}
            for (sid, target), row in sorted(recovered.items())
        ],
        "original_sources": sorted({o["path"] for o in original.values()}),
    })
    save_atomic(output / "assessment.json", assessment)
    return assessment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-date", type=date.fromisoformat)
    args = parser.parse_args()
    now = datetime.now(TZ)
    day = args.reference_date or now.date() - timedelta(days=1)
    if day >= now.date():
        raise ValueError("D+1 test can only query a completed earlier local day")
    previous = TRIAL / day.isoformat() / "assessment.json"
    if previous.exists() and read(previous).get("passed"):
        print("Independent D+1 already validated:", previous.relative_to(ROOT))
        return
    assessment = collect(day, now)
    print("D+1 actuals trial:", day, "passed", assessment["passed"],
          "original", assessment["original_values_present"],
          "recovered", assessment["dplus1_values_present"],
          "lost", len(assessment["original_values_missing_in_dplus1"]),
          "changed", len(assessment["original_values_changed_in_dplus1"]),
          "errors", len(assessment["provider_errors"]))


if __name__ == "__main__":
    main()
