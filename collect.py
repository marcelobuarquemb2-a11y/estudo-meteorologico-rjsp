#!/usr/bin/env python3
"""Auditable forecast snapshots; missing observations are never zero."""
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from compare_forecasts import MODEL_NAMES, values_present
from collection_schedule import SCHEDULE_HOURS as WINDOW_SCHEDULE, needs_query
from study_config import HORIZON_HOURS, ROOT, SERIES_START, SLOT_HOURS, STATIONS, TZ, local_time, within_slot

MODELS=("ncep_gfs_global","icon_global","ecmwf_ifs")
VARS=("wind_speed_10m","wind_direction_10m","wind_gusts_10m","precipitation")
SCHEDULE_HOURS = {
    **WINDOW_SCHEDULE,
    "31,41,51 11 * * *": 9, "1,11,21 12 * * *": 9,
    "31,41,51 14 * * *": 12, "1,11,21 15 * * *": 12,
    "31,41,51 17 * * *": 15, "1,11,21 18 * * *": 15,
    "31,41,51 20 * * *": 18, "1,11,21 21 * * *": 18,
    # Also recognize delayed events from the previous schedule.
    "45 11 * * *": 9, "45 14 * * *": 12, "45 17 * * *": 15, "45 20 * * *": 18,
}

def save_json_atomic(path, report):
    """Checkpoint and audit writes are atomic within the GitHub runner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def meteoblue_collection_complete(report, nominal_issue, targets):
    """Meteoblue coverage must not depend on an unrelated Open-Meteo outage."""
    # Legacy offline fixtures have no source-level timestamps; real captures
    # must always validate the actual Meteoblue acquisition times.
    if collection_complete(report, nominal_issue, targets) and all(
        not site.get("sources") for site in report.get("station_results", {}).values()
    ):
        return True
    if (
        not report.get("official_series_eligible")
        or report.get("official_series_start_local") != SERIES_START.isoformat()
        or local_time(report["nominal_issue_at_local"]) != nominal_issue
        or not within_slot(local_time(report["consulted_at"]), nominal_issue)
    ):
        return False
    meteoblue_model = next(model for model in MODEL_NAMES if model.startswith("Meteoblue"))
    for station in STATIONS:
        if not station.get("enabled", True):
            continue
        site = report.get("station_results", {}).get(station["id"], {})
        if (site.get("lat"), site.get("lon")) != (station["lat"], station["lon"]):
            return False
        for package in ("meteoblue_basic-1h", "meteoblue_wind-3h"):
            source = site.get("sources", {}).get(package, {})
            try:
                acquired = dt.datetime.fromisoformat(source["retrieved_at"]).astimezone(TZ)
                if (not within_slot(acquired, nominal_issue)
                        or source["raw"]["metadata"]["utc_timeoffset"] != -3):
                    return False
            except (KeyError, TypeError, ValueError, AttributeError):
                return False
        found = {
            row.get("target_local")
            for row in site.get("forecasts", [])
            if row.get("model") == meteoblue_model
            and row.get("target_local") in targets
            and values_present(row.get("values") or {}) == len(VARS)
            and row.get("valid_at_local")
            and within_slot(local_time(row["valid_at_local"]), local_time(row["target_local"]))
        }
        if not set(targets).issubset(found):
            return False
    return True


def meteoblue_capture_state(nominal_issue, targets):
    """Best existing complete Meteoblue snapshot plus nominal refresh state."""
    _, nominal_attempted = capture_state(nominal_issue, targets)
    folder = ROOT / "data" / nominal_issue.strftime("%Y-%m") / nominal_issue.strftime("%Y-%m-%d")
    for path in sorted(folder.glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            if meteoblue_collection_complete(report, nominal_issue, targets):
                return path, nominal_attempted
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    return None, nominal_attempted


def collection_complete(report, nominal_issue, targets):
    if (
        not report.get("official_series_eligible")
        or report.get("official_series_start_local") != SERIES_START.isoformat()
        or local_time(report["nominal_issue_at_local"]) != nominal_issue
        or not within_slot(local_time(report["consulted_at"]), nominal_issue)
    ):
        return False
    expected = {(model, target) for model in MODEL_NAMES for target in targets}
    for station in STATIONS:
        if not station.get("enabled", True):
            continue
        saved = report.get("station_results", {}).get(station["id"], {})
        if (
            (saved.get("lat"), saved.get("lon")) != (station["lat"], station["lon"])
            or not saved.get("notation_audit", {}).get("passed")
        ):
            return False
        complete_rows = {
            (row.get("model"), row.get("target_local"))
            for row in saved.get("forecasts", [])
            if values_present(row.get("values") or {}) == len(VARS)
            and row.get("target_local") in targets
            and within_slot(
                local_time(row.get("valid_at_local") or row["target_local"]),
                local_time(row["target_local"]),
            )
        }
        if not expected.issubset(complete_rows):
            return False
    return True


def capture_state(nominal_issue, targets):
    """Find a complete fallback and whether a nominal refresh was already attempted."""
    folder = ROOT / "data" / nominal_issue.strftime("%Y-%m") / nominal_issue.strftime("%Y-%m-%d")
    complete = None
    nominal_attempted = False
    for path in sorted(folder.glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            if (
                not report.get("official_series_eligible")
                or report.get("official_series_start_local") != SERIES_START.isoformat()
                or local_time(report["nominal_issue_at_local"]) != nominal_issue
                or not within_slot(local_time(report["consulted_at"]), nominal_issue)
            ):
                continue
            nominal_attempted |= local_time(report["consulted_at"]) >= nominal_issue
            if collection_complete(report, nominal_issue, targets):
                complete = complete or path
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            print("Ignoring unusable audit artifact:", path.name)
    return complete, nominal_attempted


def completed_capture(nominal_issue, targets):
    return capture_state(nominal_issue, targets)[0]


def fetch(url,params):
    request=urllib.request.Request(url+"?"+urllib.parse.urlencode(params),headers={"User-Agent":"RJSP-Weather-Research/1.0"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if attempt or exc.code not in (408, 429, 500, 502, 503, 504):
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt:
                raise
        time.sleep(3)

def requested_push_capture(event, nominal_issue):
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event != "push" or not event_path:
        return False
    payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    message = (payload.get("head_commit") or {}).get("message", "")
    expected = f"Capture official forecast at {nominal_issue:%Y-%m-%d %H:%M} BRT"
    return message.partition("\n")[0] == expected


def main():
    now=dt.datetime.now(TZ)
    cron = os.environ.get("GITHUB_EVENT_SCHEDULE", "")
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    scheduled_slot = SCHEDULE_HOURS.get(cron)
    top_of_hour = now.replace(minute=0, second=0, microsecond=0)
    if scheduled_slot is None:
        candidates = (top_of_hour.replace(hour=hour) for hour in SLOT_HOURS)
        nominal_issue = min(candidates, key=lambda slot: abs(now - slot))
    else:
        nominal_issue = top_of_hour.replace(hour=scheduled_slot)
    early_worker = os.environ.get("FORECAST_EARLY_WORKER") == "1"
    if early_worker:
        nominal_issue = local_time(os.environ["FORECAST_NOMINAL_ISSUE_AT_LOCAL"])
        if nominal_issue.hour not in SLOT_HOURS or any((nominal_issue.minute, nominal_issue.second, nominal_issue.microsecond)):
            raise ValueError("Invalid explicit early-worker reference")
    targets = [
        (nominal_issue + dt.timedelta(hours=lead)).strftime("%Y-%m-%dT%H:%M")
        for lead in HORIZON_HOURS
    ]
    explicit_push = requested_push_capture(event, nominal_issue)
    diagnostic = {
        "record_type": "forecast_schedule_diagnostic",
        "nominal_issue_at_local": nominal_issue.isoformat(),
        "started_at_local": now.isoformat(),
        "schedule_event": cron or None,
        "trigger_event": event or None,
        "nominal_offset_minutes": round((now - nominal_issue).total_seconds() / 60, 3),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "status": "started",
    }
    diagnostic_path = (ROOT / "data/collection_diagnostics" / now.strftime("%Y-%m-%d")
                       / (now.strftime("%H%M%S%f") + ".json"))

    def audit(status, **details):
        if event not in ("schedule", "workflow_dispatch") and not explicit_push:
            return
        diagnostic.update(status=status, **details)
        diagnostic["updated_at_local"] = dt.datetime.now(TZ).isoformat()
        save_json_atomic(diagnostic_path, diagnostic)

    audit("started")
    official = (
        (scheduled_slot is not None or event == "workflow_dispatch" or explicit_push)
        and nominal_issue >= SERIES_START and within_slot(now, nominal_issue)
    )
    if scheduled_slot is not None or official or early_worker:
        previous, nominal_attempted = meteoblue_capture_state(nominal_issue, targets)
        if previous is not None and (
            early_worker or not official or not needs_query(now, nominal_issue, True, nominal_attempted)
        ):
            audit("skipped_complete_meteoblue", existing_archive=str(previous.relative_to(ROOT)))
            print("Complete Meteoblue capture already archived:", previous.relative_to(ROOT))
            return
    if (scheduled_slot is not None or early_worker) and not official:
        audit("missed_window")
        raise SystemExit("Scheduled capture missed the permitted time window: " + now.isoformat())
    if event == "push" and not explicit_push:
        print("Deployment verified; forecast queries wait for a scheduled acquisition")
        return

    result = {
        "consulted_at": now.isoformat(),
        "official_series_start_local": SERIES_START.isoformat(),
        "official_series_eligible": official,
        "nominal_issue_at_local": nominal_issue.isoformat(),
        "nominal_slot_hour_brt": nominal_issue.hour,
        "consultation_offset_minutes": round((now - nominal_issue).total_seconds() / 60, 4),
        "schedule_event": cron or None,
        "trigger_event": event or None,
        "explicit_push_capture": explicit_push,
        "nominal_refresh_attempt": now >= nominal_issue,
        "information_time_convention": "actual consultation time",
        "collection_scope": "meteoblue_only",
        "targets_local": targets,
        "station_results": {},
        "note": "Raw forecasts only; no observations or skill scores",
    }
    # A local checkpoint is refreshed after every source response. The workflow
    # archives it on recoverable process failure, then deletes it after the final
    # immutable forecast snapshot has been written.
    checkpoint_path = (ROOT / "data/collection_checkpoints" / now.strftime("%Y-%m-%d")
                       / (now.strftime("%H%M%S%f") + ".json"))

    def checkpoint():
        save_json_atomic(checkpoint_path, result)

    key=os.environ.get("METEOBLUE_API_KEY")
    for st in STATIONS:
        item={"name":st["name"],"lat":st["lat"],"lon":st["lon"],"sources":{},"errors":[],
              "coordinate_status":st.get("coordinate_status"),
              "forecast_acquisition_validated":st.get("forecast_acquisition_validated",False)}
        result["station_results"][st["id"]]=item
        if not st.get("enabled",True):
            item["status"]="excluded_from_official_series"
        elif st["lat"] is None or st["lon"] is None:
            item["errors"].append("Coordinates not independently verified")
    checkpoint()

    # Only time-sensitive Meteoblue forecasts are acquired in this workflow.
    # Each source response is preserved before the next external request begins.
    for st in STATIONS:
        item=result["station_results"][st["id"]]
        if item.get("status") or st["lat"] is None or st["lon"] is None:
            continue
        if not key:
            item["errors"].append("Meteoblue secret not configured")
            checkpoint()
            continue
        for package in ("basic-1h","wind-3h"):
            if official and not within_slot(dt.datetime.now(TZ), nominal_issue):
                item["errors"].append("Meteoblue "+package+" skipped: window already closed")
                checkpoint()
                continue
            try:
                raw=fetch("https://my.meteoblue.com/packages/"+package,
                          {"lat":st["lat"],"lon":st["lon"],"asl":0,"tz":"America/Sao_Paulo",
                           "format":"json","apikey":key})
                item["sources"]["meteoblue_"+package]={
                    "retrieved_at":dt.datetime.now(dt.timezone.utc).isoformat(),
                    "raw":raw,"model_run":raw.get("metadata",{}).get("modelrun_utc"),
                    "mlm_provenance":"not verified","target_times":targets}
            except Exception as e:
                item["errors"].append("Meteoblue "+package+" "+type(e).__name__)
            checkpoint()

    # ICON, GFS and ECMWF are collected exclusively by collect_previous_day.py.
    # Both scheduled and early real-time workers query Meteoblue only.
    # Preserve raw API responses and generate normalized, audited forecast records.
    from normalize_collection import normalize_collection
    normalize_collection(result)
    out=ROOT/"data"/now.strftime("%Y-%m")/now.strftime("%Y-%m-%d")
    out.mkdir(parents=True,exist_ok=True)
    path=out/(now.strftime("%H%M%S%f" if early_worker else "%H%M%S")+".json")
    save_json_atomic(path, result)
    # The fully archived snapshot supersedes the local incremental checkpoint.
    checkpoint_path.unlink(missing_ok=True)
    print("Audit artifact:",path.relative_to(ROOT))
    print("Sources with numeric responses:",sum(len(x["sources"]) for x in result["station_results"].values()))
    print("Stations awaiting verified coordinates:",sum(x["lat"] is None for x in result["station_results"].values()))
    mb_count = sum(
        values_present(row.get("values") or {})
        for station in result["station_results"].values()
        for row in station.get("forecasts", [])
        if str(row.get("model", "")).startswith("Meteoblue")
    )
    result_complete = meteoblue_collection_complete(result, nominal_issue, targets)
    audit("complete_meteoblue" if result_complete else "partial_meteoblue",
          meteoblue_values_present=mb_count, meteoblue_values_expected=len(STATIONS)*len(targets)*len(VARS),
          archive=str(path.relative_to(ROOT)))
    if official and not result_complete and meteoblue_capture_state(nominal_issue, targets)[0] is None:
        raise SystemExit("Incomplete Meteoblue capture archived; later attempts can retry")

if __name__=="__main__":
    main()


