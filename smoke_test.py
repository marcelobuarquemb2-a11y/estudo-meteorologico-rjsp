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


def validate_time_source(source, metadata, times, targets, *, meteoblue=False):
    """Fail closed on ambiguous offsets, dates, duplicate times or timezone drift."""
    expected_offset=int(dt.datetime.now(TZ).utcoffset().total_seconds())
    if meteoblue:
        hours=metadata.get("utc_timeoffset")
        abbreviation=metadata.get("timezone_abbrevation")
        if not isinstance(hours,(int,float)) or int(hours*3600)!=expected_offset:
            raise ValueError(source+": unexpected UTC offset in hours")
        if abbreviation!="GMT-03":
            raise ValueError(source+": unexpected timezone abbreviation")
        fmt="%Y-%m-%d %H:%M"
        normalized=[t.replace("T"," ") for t in targets]
    else:
        if metadata.get("timezone")!="America/Sao_Paulo":
            raise ValueError(source+": unexpected timezone")
        if metadata.get("utc_offset_seconds")!=expected_offset:
            raise ValueError(source+": unexpected UTC offset in seconds")
        fmt="%Y-%m-%dT%H:%M"
        normalized=targets
    if not isinstance(times,list) or len(times)!=len(set(times)):
        raise ValueError(source+": missing or duplicate time axis")
    for t in times:
        parsed=dt.datetime.strptime(t,fmt).replace(tzinfo=TZ)
        utc=parsed.astimezone(dt.timezone.utc)
        if utc.astimezone(TZ)!=parsed:
            raise ValueError(source+": failed local-UTC-local round trip")
        if parsed.utcoffset().total_seconds()!=expected_offset:
            raise ValueError(source+": inconsistent offset on "+t)
    for t in normalized:
        if t not in times:
            raise ValueError(source+": target time missing: "+t)
    return {"source":source,"offset_seconds":expected_offset,
            "targets_utc":[dt.datetime.strptime(t,fmt).replace(tzinfo=TZ)
                           .astimezone(dt.timezone.utc).isoformat() for t in normalized],
            "passed":True}

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
        report.setdefault("time_validation",[]).append(validate_time_source("Open-Meteo",raw,hourly.get("time",[]),targets))
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
                axis="data_1h" if package=="basic-1h" else "data_3h"
                report.setdefault("time_validation",[]).append(validate_time_source("Meteoblue "+package,raw.get("metadata",{}),raw.get(axis,{}).get("time",[]),targets,meteoblue=True))
                save("meteoblue_"+package+"_raw.json",raw)
                report.setdefault("meteoblue_metadata",{})[package]=raw.get("metadata")
            except Exception as exc:
                # Never log URL, request, or API key.
                report["errors"].append({"source":"Meteoblue "+package,"type":type(exc).__name__})
    # Meteoblue package parsing is based on the persisted 2026-11-04 response schema.
    # The package does NOT establish that the values originate from Meteoblue AI/mLM.
    basic_path=folder/"meteoblue_basic-1h_raw.json"
    wind_path=folder/"meteoblue_wind-3h_raw.json"
    basic=json.loads(basic_path.read_text(encoding="utf-8")) if basic_path.exists() else {}
    wind=json.loads(wind_path.read_text(encoding="utf-8")) if wind_path.exists() else {}
    basic_data=basic.get("data_1h",{})
    wind_data=wind.get("data_3h",{})
    basic_times=basic_data.get("time",[])
    wind_times=wind_data.get("time",[])
    unit_ok=(basic.get("units",{}).get("windspeed")=="ms-1"
             and basic.get("units",{}).get("precipitation")=="mm")
    def at(data,times,field,target):
        time=target.replace("T"," ")
        if time not in times:
            return None
        idx=times.index(time)
        vals=data.get(field,[])
        return vals[idx] if idx<len(vals) else None
    for horizon,target in zip((24,48,72),targets):
        direction=at(basic_data,basic_times,"winddirection",target)
        speed=at(basic_data,basic_times,"windspeed",target)
        rain=at(basic_data,basic_times,"precipitation",target)
        gust=at(wind_data,wind_times,"gust",target)
        values={"direction_deg":direction,
                "mean_kt":round(speed*1.9438444924,3) if speed is not None and unit_ok else None,
                "gust_kt":round(gust*1.9438444924,3) if gust is not None and unit_ok else None,
                "precip_mm":rain}
        report["results"].append({"model":"Meteoblue (package; AI unverified)",
            "horizon_hours":horizon,"valid_local":target,"values":values,
            "field_keys":{"direction_deg":"data_1h.winddirection",
                          "mean_kt":"data_1h.windspeed","gust_kt":"data_3h.gust",
                          "precip_mm":"data_1h.precipitation"},
            "gust_height":"unverified","model_identity":"AI/mLM unverified",
            "conversion":"m/s to kt * 1.9438444924"})
    if len(report.get("time_validation",[]))!=3:
        report["errors"].append({"source":"Time validation","type":"IncompleteValidation","detail":"Not all three source time axes passed checks"})
    report["scalar_count_non_null"]=sum(v is not None for row in report["results"] for v in row["values"].values())
    report["scalar_count_expected"]=48
    report["data_completeness_passed"]=report["scalar_count_non_null"]==48 and not report["errors"]
    report["validation_passed"]=False
    report["validation_blockers"]=["Meteoblue AI/mLM identity unverified",
        "Meteoblue gust reference height/definition unverified",
        "Manual test times are next calendar days, not exact 24/48/72h from query timestamp"]
    (folder/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Test artifacts:",folder.relative_to(ROOT))
    print("Non-null forecast scalars:",report["scalar_count_non_null"],"/48")
    print("Errors:",json.dumps(report["errors"],ensure_ascii=False))
    print("Full validation passed:",report["validation_passed"])

if __name__=="__main__":
    main()
