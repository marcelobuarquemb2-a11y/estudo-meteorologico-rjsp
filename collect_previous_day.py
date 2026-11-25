#!/usr/bin/env python3
"""Collect yesterday's four reference slots from individual forecast model runs.

Original real-time files and Meteoblue are never modified. Each invocation is
one attempt; GitHub schedules ten opportunities from 09:00 through 12:00 BRT.
"""
import argparse
import json
import math
from decimal import Decimal, ROUND_HALF_UP
import os
from datetime import date, datetime, timedelta, timezone
import urllib.parse
import urllib.request

from compare_forecasts import forecast_archives
from normalize_collection import distance
from study_config import HORIZON_HOURS, ROOT, SERIES_START, SLOT_HOURS, STATIONS, TZ

API = "https://single-runs-api.open-meteo.com/v1/forecast"
MODELS = {"ICON": "icon_global", "GFS": "ncep_gfs_global", "ECMWF": "ecmwf_ifs"}
FIELDS = {"direction_deg": "wind_direction_10m", "mean_kt": "wind_speed_10m",
          "gust_kt": "wind_gusts_10m", "precip_mm": "precipitation"}
UNITS = {"direction_deg": "°", "mean_kt": "kn", "gust_kt": "kn", "precip_mm": "mm"}
# UTC initialisations reproduced in the study. Snapshot calibration can identify
# another run; the fixed rule is used only when there is no retained snapshot.
RUN_HOURS = {9: {"ICON": 6, "GFS": 6, "ECMWF": 0},
             12: {"ICON": 6, "GFS": 6, "ECMWF": 6},
             15: {"ICON": 12, "GFS": 12, "ECMWF": 6},
             18: {"ICON": 12, "GFS": 12, "ECMWF": 12}}
MAX_ATTEMPTS = 10
POLICY = "rjsp_calibrated_reference_cycles_v1"
SNAPSHOT_COMPARISON_POLICY = "uniform_precip_1dp_v1"
RETROSPECTIVE_ONLY_FROM = datetime(2026, 11, 25, 15, tzinfo=TZ)


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


def valid_value(field, value):
    return numeric(value) and (0 <= value <= 360 if field == "direction_deg" else value >= 0)


def read(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def archive_path(root, issue):
    return root / "data/recovered_forecasts" / issue.strftime("%Y-%m-%d/%H%M.json")


def fetch(params):
    url = API + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "RJSP-Daily-Retrospective/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def targets(issue):
    return [(issue + timedelta(hours=lead)).strftime("%Y-%m-%dT%H:%M")
            for lead in HORIZON_HOURS]


def run_label(issue, model):
    return issue.astimezone(timezone.utc).replace(
        hour=RUN_HOURS[issue.hour][model], minute=0, second=0, microsecond=0
    ).strftime("%Y-%m-%dT%H:%M")


def request_params(issue, model, run, stations):
    return {"latitude": ",".join(str(s["lat"]) for s in stations),
            "longitude": ",".join(str(s["lon"]) for s in stations),
            "models": MODELS[model], "run": run,
            "hourly": ",".join(FIELDS.values()), "timezone": "America/Sao_Paulo",
            "wind_speed_unit": "kn", "forecast_days": 6}


def extract(response, issue, model, stations):
    reports = response if isinstance(response, list) else [response]
    if len(reports) != len(stations):
        raise ValueError("Historical response has the wrong station count")
    result = {}
    for station, report in zip(stations, reports):
        sid = station["id"]
        if report.get("error"):
            raise ValueError("Provider error: " + str(report.get("reason", "unknown")))
        lat, lon = report.get("latitude"), report.get("longitude")
        if not all(numeric(x) for x in (lat, lon)) or distance(
                (station["lat"], station["lon"]), (lat, lon)) > 30:
            raise ValueError("Historical grid is not compatible with station " + sid)
        if report.get("utc_offset_seconds") != -10800:
            raise ValueError("Historical timestamps must use Brasilia UTC-3")
        hourly, units = report.get("hourly") or {}, report.get("hourly_units") or {}
        times = hourly.get("time") or []
        if len(times) != len(set(times)):
            raise ValueError("Duplicate historical valid times")
        index = {value: i for i, value in enumerate(times)}
        rows = []
        for lead, target in zip(HORIZON_HOURS, targets(issue)):
            pos = index.get(target)
            values = {}
            for field, api_field in FIELDS.items():
                key = api_field if api_field in hourly else api_field + "_" + MODELS[model]
                array = hourly.get(key) or []
                value = array[pos] if pos is not None and pos < len(array) else None
                values[field] = value if units.get(key) == UNITS[field] and valid_value(field, value) else None
            rows.append({"model": model, "source": "Open-Meteo Single Runs historical",
                         "day_offset": lead // 24, "nominal_horizon_hours": lead,
                         "target_local": target, "valid_at_local": target,
                         "values": values})
        result[sid] = {"grid": [lat, lon], "forecasts": rows}
    return result


def rows_for(payload, sid, model):
    return [r for r in (payload.get("station_results", {}).get(sid, {}).get("forecasts") or [])
            if r.get("model") == model]


def group_values(payload, model, issue, stations):
    count = 0
    for station in stations:
        stored = payload.get("station_results", {}).get(station["id"], {})
        if (stored.get("lat"), stored.get("lon")) != (station["lat"], station["lon"]):
            continue
        rows = rows_for(payload, station["id"], model)
        for lead, target in zip(HORIZON_HOURS, targets(issue)):
            matches = [r for r in rows if r.get("nominal_horizon_hours", r.get("day_offset", 0) * 24) == lead
                       and r.get("target_local") == target]
            if len(matches) == 1:
                count += sum(valid_value(f, matches[0].get("values", {}).get(f)) for f in FIELDS)
    return count


def empty_archive(issue, stations):
    result = {"record_type": "historical_forecast_recovery",
              "nominal_issue_at_local": issue.isoformat(), "operational_collection": False,
              "official_series_eligible": False, "accepted_for_study": True,
              "collection_method": "daily_retrospective", "recovery_status": "daily_retrospective",
              "historical_round_publication_verified": False,
              "method": "Open-Meteo Single Runs API, model-specific historical run",
              "run_selection_policy": POLICY,
              "model_runs_utc": {model: run_label(issue, model) for model in MODELS},
              "model_acquisition": {},
              "station_results": {}}
    for station in stations:
        result["station_results"][station["id"]] = {
            "name": station["name"], "lat": station["lat"], "lon": station["lon"],
            "grid_by_model": {}, "forecasts": [
                {"model": model, "source": "Open-Meteo Single Runs historical",
                 "day_offset": lead // 24, "nominal_horizon_hours": lead,
                 "target_local": target, "valid_at_local": target,
                 "values": {field: None for field in FIELDS}}
                for model in MODELS for lead, target in zip(HORIZON_HOURS, targets(issue))]}
    return result


def snapshot_values(original, model, issue, stations):
    values = {}
    sites = original[3] if original else {}
    for station in stations:
        site = sites.get(station["id"], {})
        if (site.get("lat"), site.get("lon")) != (station["lat"], station["lon"]):
            continue
        for row in site.get("forecasts", []):
            if row.get("model") != model or row.get("target_local") not in targets(issue):
                continue
            for field, value in row.get("values", {}).items():
                if field in FIELDS and valid_value(field, value):
                    values[(station["id"], row["target_local"], field)] = value
    return values


def snapshot_match(field, value, previous):
    if not numeric(value) or previous is None:
        return False
    if abs(value - previous) < 1e-8:
        return True
    if field == "precip_mm":
        quantum = Decimal("0.1")
        return (Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
                == Decimal(str(previous)).quantize(quantum, rounding=ROUND_HALF_UP))
    return False


def select_group(issue, model, stations, original, existing, fetcher):
    # From the definitive D+1 cutover, no real-time snapshot is required or used
    # to select the model run. Historical records remain available for audit.
    if issue >= RETROSPECTIVE_ONLY_FROM:
        original = None
    reference = snapshot_values(original, model, issue, stations)
    locked = existing.get("model_runs_utc", {}).get(model)
    has_previous = group_values(existing, model, issue, stations) > 0
    policy_run = run_label(issue, model)
    runs = [locked if locked and has_previous else policy_run]
    if reference and not has_previous:
        utc = issue.astimezone(timezone.utc)
        latest = utc.replace(hour=(utc.hour // 6) * 6, minute=0, second=0, microsecond=0)
        runs.extend((latest - timedelta(hours=6 * i)).strftime("%Y-%m-%dT%H:%M") for i in range(4))
    runs = list(dict.fromkeys(runs))
    options, requests, failures = [], [], []
    for run in runs:
        params = request_params(issue, model, run, stations)
        url = API + "?" + urllib.parse.urlencode(params)
        try:
            data = extract(fetcher(params), issue, model, stations)
            present, matches, compared_present = 0, 0, 0
            differences = []
            for sid, site in data.items():
                for row in site["forecasts"]:
                    for field, value in row["values"].items():
                        present += int(valid_value(field, value))
                        previous = reference.get((sid, row["target_local"], field))
                        compared_present += int(numeric(value) and previous is not None)
                        matches += int(snapshot_match(field, value, previous))
                        if numeric(value) and previous is not None and abs(value - previous) >= 1e-8:
                            differences.append({"station_id": sid, "target_local": row["target_local"],
                                                "variable": field, "snapshot": previous, "retrieved": value,
                                                "matches_after_precision_normalization": snapshot_match(field, value, previous)})
            accepted = (not reference or has_previous or
                        (matches > 0 and (run == policy_run or compared_present == len(reference))
                         and all(d["matches_after_precision_normalization"] for d in differences)))
            if accepted:
                options.append((matches, present, run, data, differences))
            requests.append({"run_utc": run, "url": url, "values_present": present,
                             "snapshot_matches": matches, "snapshot_compared": len(reference),
                             "snapshot_comparison_policy": SNAPSHOT_COMPARISON_POLICY,
                             "snapshot_raw_differences": len(differences),
                             "selection_accepted": accepted,
                             "snapshot_difference_details": differences})
            if accepted and present == len(stations) * len(HORIZON_HOURS) * len(FIELDS) and (
                    not reference or matches == len(reference) or has_previous):
                break
        except Exception as exc:
            failures.append({"run_utc": run, "url": url, "error": type(exc).__name__,
                             "message": str(exc)[:500]})
    if not options:
        return None, {"requests": requests, "errors": failures}
    # Prefer reproducibility, then completeness. A tie retains the calibrated
    # policy instead of silently choosing a newer, potentially unavailable run.
    best = max(options, key=lambda x: (x[0], x[1], x[2] == policy_run))
    matches, present, run, data, differences = best
    return (run, data), {"requests": requests, "errors": failures,
                        "selection_accepted": True,
                        "selection_basis": "snapshot_correspondence" if reference else POLICY,
                        "snapshot_matches": matches, "snapshot_compared": len(reference),
                        "snapshot_differences": len(reference) - matches,
                        "snapshot_comparison_policy": SNAPSHOT_COMPARISON_POLICY,
                        "snapshot_raw_differences": len(differences),
                        "snapshot_difference_details": differences,
                        "original_snapshot_file": original[2] if original else None}


def collect_day(reference_date, *, root=ROOT, now=None, fetcher=fetch, originals=None, stations=None):
    now = now or datetime.now(TZ)
    now = now.astimezone(TZ)
    if reference_date >= now.date():
        raise ValueError("Reference date must be a completed previous local day")
    stations = stations or [s for s in STATIONS if s.get("enabled", True)]
    issues = [datetime.combine(reference_date, datetime.min.time(), tzinfo=TZ).replace(hour=h)
              for h in SLOT_HOURS]
    if any(issue < SERIES_START for issue in issues):
        raise ValueError("Date precedes the study's first complete collection day")
    report_path = root / "data/daily_recovery" / str(reference_date) / "status.json"
    report = read(report_path, {"reference_date": str(reference_date), "attempts": []})
    archives = {issue: read(archive_path(root, issue), empty_archive(issue, stations)) for issue in issues}
    # The first live run exposed an unsafe fallback following a network timeout.
    # Retain that evidence separately, remove only the unvalidated model group,
    # and let the next normal attempt obtain the intended historical cycle.
    for issue, payload in archives.items():
        for model, evidence in list(payload.get("model_acquisition", {}).items()):
            if evidence.get("snapshot_differences", 0) and not evidence.get("selection_accepted"):
                rejected = root / "data/daily_recovery" / str(reference_date) / "rejected_runs" / f"{issue:%H%M}_{model}.json"
                save(rejected, {"reason": "Run not validated against retained snapshot after provider timeout",
                                "issue_local": issue.isoformat(), "model": model,
                                "model_run_utc": payload.get("model_runs_utc", {}).get(model),
                                "evidence": evidence,
                                "station_results": {sid: {"forecasts": rows_for(payload, sid, model)}
                                                    for sid in payload["station_results"]}})
                for station in payload["station_results"].values():
                    for row in station.get("forecasts", []):
                        if row.get("model") == model:
                            row["values"] = {field: None for field in FIELDS}
                            row.pop("run_utc", None)
                payload["model_runs_utc"][model] = run_label(issue, model)
                del payload["model_acquisition"][model]
                save(archive_path(root, issue), payload)
    expected_group = len(stations) * len(HORIZON_HOURS) * len(FIELDS)
    expected = len(issues) * len(MODELS) * expected_group

    def coverage():
        return sum(group_values(p, model, issue, stations)
                   for issue, p in archives.items() for model in MODELS)

    def finish(state):
        present = coverage()
        report.update({"record_type": "daily_retrospective_collection_status",
                       "updated_at_local": now.isoformat(), "status": state,
                       "attempt_count": len(report["attempts"]), "maximum_attempts": MAX_ATTEMPTS,
                       "retry_interval_minutes": 20, "start_hour_brt": 9,
                       "models": list(MODELS), "station_ids": [s["id"] for s in stations],
                       "reference_hours_brt": list(SLOT_HOURS),
                       "forecast_values_present": present, "forecast_values_expected": expected,
                       "complete": present == expected,
                       "archives": [str(archive_path(root, i).relative_to(root)) for i in issues]})
        save(report_path, report)
        return report

    if coverage() == expected and report.get("complete"):
        return {**report, "_changed": False}
    if coverage() == expected:
        return finish("complete")
    if len(report["attempts"]) >= MAX_ATTEMPTS:
        if report.get("status") == "attempts_exhausted":
            return {**report, "_changed": False}
        return finish("attempts_exhausted")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if run_id and any(a.get("github_run_id") == run_id for a in report["attempts"]):
        return {**report, "_changed": False}
    unreadable = []
    if originals is None:
        originals = (forecast_archives(unreadable)
                     if any(issue < RETROSPECTIVE_ONLY_FROM for issue in issues) else {})
    attempt = {"number": len(report["attempts"]) + 1, "started_at_local": now.isoformat(),
               "github_run_id": run_id, "groups": [], "unreadable_original_archives": unreadable}
    for issue, payload in archives.items():
        if payload.get("nominal_issue_at_local") != issue.isoformat():
            raise ValueError("Recovered archive reference does not match its path")
        for model in MODELS:
            before = group_values(payload, model, issue, stations)
            if before == expected_group:
                continue
            selected, evidence = select_group(issue, model, stations, originals.get(issue), payload, fetcher)
            attempt["groups"].append({"issue_local": issue.isoformat(), "model": model, **evidence})
            if selected is None:
                continue
            run, data = selected
            for station in stations:
                sid = station["id"]
                stored = payload["station_results"][sid]
                if (stored.get("lat"), stored.get("lon")) != (station["lat"], station["lon"]):
                    raise ValueError("Stored recovery coordinates changed: " + sid)
                stored.setdefault("grid_by_model", {})[model] = data[sid]["grid"]
                for new_row in data[sid]["forecasts"]:
                    old_row = next(r for r in stored["forecasts"] if r["model"] == model
                                   and r["day_offset"] == new_row["day_offset"])
                    for field, value in new_row["values"].items():
                        if not valid_value(field, old_row["values"].get(field)) and valid_value(field, value):
                            old_row["values"][field] = value
                    old_row.update({"run_utc": run, "provenance": "daily_retrospective",
                                    "original_publication_at_slot_verified": False})
            payload.setdefault("model_runs_utc", {})[model] = run
            payload.setdefault("model_acquisition", {})[model] = {
                "retrieved_at_local": now.isoformat(), "selection_policy": POLICY, **evidence}
            payload["last_retrieved_at_local"] = now.isoformat()
            save(archive_path(root, issue), payload)
    report["attempts"].append(attempt)
    present = coverage()
    state = "complete" if present == expected else (
        "attempts_exhausted" if len(report["attempts"]) >= MAX_ATTEMPTS else "partial")
    return finish(state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=date.fromisoformat, help="Reference day (default: yesterday in Brasilia)")
    args = parser.parse_args()
    now = datetime.now(TZ)
    reference = args.date or (now.date() - timedelta(days=1))
    report = collect_day(reference, now=now)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("changed=" + str(report.get("_changed", True)).lower() + "\n")
    print(json.dumps({key: report[key] for key in (
        "reference_date", "status", "attempt_count", "forecast_values_present", "forecast_values_expected")},
        ensure_ascii=False))
    if not report["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
