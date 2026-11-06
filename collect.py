#!/usr/bin/env python3
"""Auditable forecast snapshots; missing observations are never zero."""
import datetime as dt
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parent
TZ=ZoneInfo("America/Sao_Paulo")
MODELS=("ncep_gfs_global","icon_global","ecmwf_ifs")
VARS=("wind_speed_10m","wind_direction_10m","wind_gusts_10m","precipitation")

def fetch(url,params):
    request=urllib.request.Request(url+"?"+urllib.parse.urlencode(params),headers={"User-Agent":"RJSP-Weather-Research/1.0"})
    with urllib.request.urlopen(request,timeout=45) as response:
        return json.load(response)

def main():
    now=dt.datetime.now(TZ)
    start=dt.datetime(2026,11,6,15,0,tzinfo=TZ)
    # A delayed scheduled job keeps its requested nominal slot distinct from API retrieval time.
    cron=os.environ.get("GITHUB_EVENT_SCHEDULE","")
    slots={"0 12,15,18,21 * * *":None,"0 12 * * *":9,"0 15 * * *":12,
           "0 18 * * *":15,"0 21 * * *":18}
    scheduled_slot=slots.get(cron)
    if scheduled_slot is None:
        scheduled_slot=min((9,12,15,18),key=lambda h:abs(h-now.hour))
    targets=[(now.date()+dt.timedelta(days=d)).isoformat()+f"T{scheduled_slot:02d}:00" for d in (1,2,3)]
    official=(now>=start and now.date()>=start.date() and
              (now.date()>start.date() or scheduled_slot>=15))

    result={"consulted_at":now.isoformat(),"official_series_start_local":start.isoformat(),
            "official_series_eligible":official,"nominal_slot_hour_brt":scheduled_slot,
            "schedule_event":cron or None,"information_time_convention":"actual consultation time",
            "targets_local":targets,"station_results":{},"note":"Raw forecasts only; no observations or skill scores"}
    key=os.environ.get("METEOBLUE_API_KEY")
    for st in json.loads((ROOT/"stations.json").read_text(encoding="utf-8")):
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
