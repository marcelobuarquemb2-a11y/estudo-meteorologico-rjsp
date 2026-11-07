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
from study_config import HORIZON_HOURS, ROOT, SERIES_START, SLOT_HOURS, STATIONS, TZ

MODELS=("ncep_gfs_global","icon_global","ecmwf_ifs")
VARS=("wind_speed_10m","wind_direction_10m","wind_gusts_10m","precipitation")

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
    slots_by_cron = {"0 12 * * *": 9, "0 15 * * *": 12, "0 18 * * *": 15, "0 21 * * *": 18}
    scheduled_slot = slots_by_cron.get(cron)
    hour = scheduled_slot if scheduled_slot is not None else max(
        (slot for slot in SLOT_HOURS if slot <= now.hour), default=SLOT_HOURS[0]
    )
    nominal_issue = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    targets = [
        (nominal_issue + dt.timedelta(hours=lead)).strftime("%Y-%m-%dT%H:%M")
        for lead in HORIZON_HOURS
    ]
    explicit_push = requested_push_capture(event, nominal_issue)
    official = (
        (scheduled_slot is not None or event == "workflow_dispatch" or explicit_push)
        and SERIES_START <= nominal_issue <= now < nominal_issue + dt.timedelta(hours=3)
    )

    result = {
        "consulted_at": now.isoformat(),
        "official_series_start_local": SERIES_START.isoformat(),
        "official_series_eligible": official,
        "nominal_issue_at_local": nominal_issue.isoformat(),
        "nominal_slot_hour_brt": hour,
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

if __name__=="__main__":
    main()
