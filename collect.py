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
from study_config import HORIZON_HOURS, ROOT, SERIES_START, SLOT_HOURS, STATIONS, TZ, local_time, within_slot

MODELS=("ncep_gfs_global","icon_global","ecmwf_ifs")
VARS=("wind_speed_10m","wind_direction_10m","wind_gusts_10m","precipitation")
SCHEDULE_HOURS = {
    "31,41,51 11 * * *": 9, "1,11,21 12 * * *": 9,
    "31,41,51 14 * * *": 12, "1,11,21 15 * * *": 12,
    "31,41,51 17 * * *": 15, "1,11,21 18 * * *": 15,
    "31,41,51 20 * * *": 18, "1,11,21 21 * * *": 18,
    # Also recognize delayed events from the previous schedule.
    "45 11 * * *": 9, "45 14 * * *": 12, "45 17 * * *": 15, "45 20 * * *": 18,
}


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


def completed_capture(nominal_issue, targets):
    folder = ROOT / "data" / nominal_issue.strftime("%Y-%m") / nominal_issue.strftime("%Y-%m-%d")
    for path in sorted(folder.glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            if collection_complete(report, nominal_issue, targets):
                return path
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            print("Ignoring unusable audit artifact:", path.name)
    return None


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
    targets = [
        (nominal_issue + dt.timedelta(hours=lead)).strftime("%Y-%m-%dT%H:%M")
        for lead in HORIZON_HOURS
    ]
    explicit_push = requested_push_capture(event, nominal_issue)
    official = (
        (scheduled_slot is not None or event == "workflow_dispatch" or explicit_push)
        and nominal_issue >= SERIES_START and within_slot(now, nominal_issue)
    )
    if scheduled_slot is not None or official:
        previous = completed_capture(nominal_issue, targets)
        if previous is not None:
            print("Complete official capture already archived:", previous.relative_to(ROOT))
            return
    if scheduled_slot is not None and not official:
        raise SystemExit("Scheduled capture missed the permitted time window: " + now.isoformat())

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
        "information_time_convention": "actual consultation time",
        "targets_local": targets,
        "station_results": {},
        "note": "Raw forecasts only; no observations or skill scores",
    }
    key=os.environ.get("METEOBLUE_API_KEY")
    for st in STATIONS:
        item={"name":st["name"],"lat":st["lat"],"lon":st["lon"],"sources":{},"errors":[],
              "coordinate_status":st.get("coordinate_status"),
              "forecast_acquisition_validated":st.get("forecast_acquisition_validated",False)}
        result["station_results"][st["id"]]=item
        if st.get("enabled",True) is False:
            item["status"]="excluded_from_official_series"
            continue
        if st["lat"] is None or st["lon"] is None:
            item["errors"].append("Coordinates not independently verified")
            continue
        params={"latitude":st["lat"],"longitude":st["lon"],"hourly":",".join(VARS),
                "models":",".join(MODELS),"wind_speed_unit":"kn",
                "timezone":"America/Sao_Paulo","forecast_days":5}
        try:
            raw=fetch("https://api.open-meteo.com/v1/forecast",params)
            item["sources"]["open_meteo"]={"retrieved_at":dt.datetime.now(dt.timezone.utc).isoformat(),
                "raw":raw,"target_times":targets,"model_run":"not reported explicitly"}
        except Exception as e:
            item["errors"].append("Open-Meteo "+type(e).__name__)
        if not key:
            item["errors"].append("Meteoblue secret not configured")
            continue
        for package in ("basic-1h","wind-3h"):
            try:
                raw=fetch("https://my.meteoblue.com/packages/"+package,
                          {"lat":st["lat"],"lon":st["lon"],"asl":0,"tz":"America/Sao_Paulo",
                           "format":"json","apikey":key})
                item["sources"]["meteoblue_"+package]={"retrieved_at":dt.datetime.now(dt.timezone.utc).isoformat(),
                    "raw":raw,"model_run":raw.get("metadata",{}).get("modelrun_utc"),
                    "mlm_provenance":"not verified","target_times":targets}
            except Exception as e:
                item["errors"].append("Meteoblue "+package+" "+type(e).__name__)
    # Preserve raw API responses and generate normalized, audited forecast records.
    from normalize_collection import normalize_collection
    normalize_collection(result)
    out=ROOT/"data"/now.strftime("%Y-%m")/now.strftime("%Y-%m-%d")
    out.mkdir(parents=True,exist_ok=True)
    path=out/(now.strftime("%H%M%S")+".json")
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Audit artifact:",path.relative_to(ROOT))
    print("Sources with numeric responses:",sum(len(x["sources"]) for x in result["station_results"].values()))
    print("Stations awaiting verified coordinates:",sum(x["lat"] is None for x in result["station_results"].values()))
    if official and not collection_complete(result, nominal_issue, targets):
        raise SystemExit("Incomplete official capture archived; later scheduled attempts can retry")

if __name__=="__main__":
    main()
