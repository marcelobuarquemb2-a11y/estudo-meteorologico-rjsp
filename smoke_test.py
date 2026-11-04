#!/usr/bin/env python3
"""Isolated four-source API acquisition/persistence smoke test at Angra."""
import datetime as dt
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parent
TZ=ZoneInfo("America/Sao_Paulo")
LAT,LON=-22.97555554,-44.30333333
MODELS={"ICON":"icon_global","GFS":"ncep_gfs_global","ECMWF":"ecmwf_ifs"}
FIELDS={"direction_deg":"wind_direction_10m","mean_kt":"wind_speed_10m","gust_kt":"wind_gusts_10m","precip_mm":"precipitation"}

def fetch(base,params):
    url=base+"?"+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={"User-Agent":"RJSP-Meteo-Research/1.0"})
    with urllib.request.urlopen(req,timeout=60) as resp:
        return json.load(resp)

def locate(hourly,field,model):
    options=[field+"_"+model,field]
    for key in options:
        if key in hourly:
            return key
    matches=[key for key in hourly if key.startswith(field+"_") and key.endswith(model)]
    return matches[0] if len(matches)==1 else None

def main():
    now=dt.datetime.now(TZ)
    # Manual tests use next three calendar days at the next standard slot.
    slots=(9,12,15,18)
    hour=min(slots,key=lambda x:abs(x-now.hour))
    targets=[(now.date()+dt.timedelta(days=d)).isoformat()+f"T{hour:02d}:00" for d in (1,2,3)]
    folder=ROOT/"data"/"smoke_tests"/now.strftime("%Y-%m-%d")/now.strftime("%H%M%S")
    folder.mkdir(parents=True,exist_ok=True)
    report={"consulted_at":now.isoformat(),"station":"INMET A628 Angra test coordinate",
            "coordinate":{"latitude":LAT,"longitude":LON},"targets_local":targets,
            "model_identity":{"ICON":"Open-Meteo icon_global","GFS":"Open-Meteo ncep_gfs_global",
                              "ECMWF":"Open-Meteo ecmwf_ifs","Meteoblue":"Package identity unverified; NOT certified AI/mLM"},
            "results":[],"errors":[],"raw_files":[]}
    def save(name,obj):
        path=folder/name
        path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        report["raw_files"].append(name)
    try:
        raw=fetch("https://api.open-meteo.com/v1/forecast",
            {"latitude":LAT,"longitude":LON,"hourly":",".join(FIELDS.values()),
             "models":",".join(MODELS.values()),"wind_speed_unit":"kn",
             "timezone":"America/Sao_Paulo","forecast_days":5})
        save("open_meteo_raw.json",raw)
        hourly=raw.get("hourly",{})
        times=hourly.get("time",[])
        for model,suffix in MODELS.items():
            for horizon,target in zip((24,48,72),targets):
                row={"model":model,"horizon_hours":horizon,"valid_local":target,"values":{},"field_keys":{}}
                idx=times.index(target) if target in times else None
                for label,field in FIELDS.items():
                    key=locate(hourly,field,suffix)
                    row["field_keys"][label]=key
                    values=hourly.get(key,[]) if key else []
                    row["values"][label]=values[idx] if idx is not None and idx<len(values) else None
                report["results"].append(row)
        report["open_meteo_grid"]={"latitude":raw.get("latitude"),"longitude":raw.get("longitude"),
                                   "elevation":raw.get("elevation"),"units":raw.get("hourly_units")}
    except Exception as exc:
        report["errors"].append({"source":"Open-Meteo","type":type(exc).__name__,"detail":str(exc)[:250]})
    key=os.getenv("METEOBLUE_API_KEY")
    if not key:
        report["errors"].append({"source":"Meteoblue","type":"MissingSecret"})
    else:
        for package in ("basic-1h","wind-3h"):
            try:
                raw=fetch("https://my.meteoblue.com/packages/"+package,
                    {"lat":LAT,"lon":LON,"asl":6,"tz":"America/Sao_Paulo",
                     "format":"json","apikey":key})
                save("meteoblue_"+package+"_raw.json",raw)
                report.setdefault("meteoblue_metadata",{})[package]=raw.get("metadata")
            except Exception as exc:
                # Never log URL, request, or API key.
                report["errors"].append({"source":"Meteoblue "+package,"type":type(exc).__name__})
    # Meteoblue remains unnormalized until package schema and model provenance are confirmed.
    for horizon,target in zip((24,48,72),targets):
        report["results"].append({"model":"Meteoblue","horizon_hours":horizon,
            "valid_local":target,"values":{field:None for field in FIELDS},
            "status":"raw packages only; schema and AI/mLM provenance pending"})
    report["scalar_count_non_null"]=sum(v is not None for row in report["results"] for v in row["values"].values())
    report["scalar_count_expected"]=48
    report["validation_passed"]=report["scalar_count_non_null"]==48 and not report["errors"]
    (folder/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Test artifacts:",folder.relative_to(ROOT))
    print("Non-null forecast scalars:",report["scalar_count_non_null"],"/48")
    print("Errors:",json.dumps(report["errors"],ensure_ascii=False))
    print("Full validation passed:",report["validation_passed"])

if __name__=="__main__":
    main()
