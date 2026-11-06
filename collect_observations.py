#!/usr/bin/env python3
"""Acquire REAL station observations only; never replace a sensor with model/grid values."""
import datetime as dt
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parent
TZ=ZoneInfo("America/Sao_Paulo")
UTC=dt.timezone.utc
KNOTS=1.9438444924
STATIONS=json.loads((ROOT/"stations.json").read_text(encoding="utf-8"))
SOURCES={
    "inmet_a628_angra":{"name":"INMET automatic station A628","status":"endpoint_trial",
        "url_template":"https://apitempo.inmet.gov.br/estacao/{start}/{end}/A628"},
    "sp1_sao_sebastiao":{"name":"SiMCosta SP-1","status":"requires_verified_data_endpoint"},
    "ilhabela":{"name":"SiMCosta Ilhabela","status":"requires_verified_meteorological_sensor"},
    "cdrj1_ilha_pai":{"name":"SiMCosta / PortosRio CDRJ-1","status":"requires_verified_data_endpoint"},
    "ilha_rasa":{"name":"Marinha 83117 / PortosRio","status":"requires_machine_readable_access"},
    "ilha_fiscal":{"name":"PortosRio SMA Ilha Fiscal","status":"requires_machine_readable_access"},
    "dois_rios":{"name":"CEADS (historical)","status":"excluded"}
}

def clean(v):
    if v is None or (isinstance(v,str) and v.strip().lower() in ("","null","none","nan","9999")):
        return None
    try:
        n=float(str(v).replace(",","."))
        return None if n in (9999,-9999) else n
    except (ValueError,TypeError):
        return None

def timestamp(row):
    # INMET fields document a UTC timestamp (YYYY-MM-DD plus HHmm).
    d=row.get("DT_MEDICAO")
    h=row.get("HR_MEDICAO")
    if not isinstance(d,str) or h is None:
        return None
    h=str(h).strip().zfill(4)
    if not (len(h)==4 and h.isdigit()):
        return None
    try:
        return dt.datetime.strptime(d+" "+h,"%Y-%m-%d %H%M").replace(tzinfo=UTC)
    except ValueError:
        return None

def normalize_inmet(payload,target):
    if not isinstance(payload,list): return {"status":"unexpected_schema","observed":None}
    selected=[]
    for row in payload:
        if not isinstance(row,dict): continue
        sid=str(row.get("CD_ESTACAO","")).upper()
        if sid!="A628":continue
        if timestamp(row)==target: selected.append(row)
    if not selected:return {"status":"target_not_available","observed":None,"records_in_response":len(payload)}
    if len(selected)!=1:return {"status":"ambiguous_duplicate_records","observed":None}
    row=selected[0]
    direction=clean(row.get("VEN_DIR"))
    speed=clean(row.get("VEN_VEL"))
    gust=clean(row.get("VEN_RAJ"))
    rain=clean(row.get("CHUVA"))
    values={
        "direction_deg":direction if direction is not None and 0<=direction<=360 else None,
        "mean_kt":round(speed*KNOTS,3) if speed is not None and 0<=speed<=90 else None,
        "gust_kt":round(gust*KNOTS,3) if gust is not None and 0<=gust<=100 else None,
        "precip_mm":rain if rain is not None and 0<=rain<=500 else None
    }
    return {"status":"observation_available" if any(x is not None for x in values.values()) else "all_variables_missing",
            "observed":values,"measurement_time_utc":target.isoformat(),
            "measurement_time_brt":target.astimezone(TZ).isoformat(),
            "units":{"wind":"kn","precipitation":"mm","direction":"degrees"},
            "raw_units":{"VEN_VEL":"m/s","VEN_RAJ":"m/s","VEN_DIR":"degree","CHUVA":"mm"},
            "field_mapping":{"direction_deg":"VEN_DIR","mean_kt":"VEN_VEL",
                             "gust_kt":"VEN_RAJ","precip_mm":"CHUVA"},
            "raw_station_code":"A628"}

def request(url):
    req=urllib.request.Request(url,headers={"User-Agent":"RJSP-Observations-Audit/1.0","Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=35) as response:
        raw=response.read()
        return response.status, response.headers.get("Content-Type",""),raw

def main():
    now=dt.datetime.now(TZ)
    slot_env=os.getenv("OBS_TARGET_BRT")
    cron=os.getenv("GITHUB_EVENT_SCHEDULE","")
    schedule_hours={"20 13 * * *":9,"20 16 * * *":12,"20 19 * * *":15,"20 22 * * *":18}
    if slot_env:
        slot=dt.datetime.fromisoformat(slot_env).replace(tzinfo=TZ)
    elif cron in schedule_hours:
        slot=now.replace(hour=schedule_hours[cron],minute=0,second=0,microsecond=0)
    else:
        slot=(now-dt.timedelta(minutes=80)).replace(minute=0,second=0,microsecond=0)
    target=slot.astimezone(UTC)
    result={"acquired_at_brt":now.isoformat(),"target_local":slot.isoformat(),
        "scheduled_cron":cron or None,"target_measurement_delayed_by_minutes":round((now-slot).total_seconds()/60,1),
        "target_utc":target.isoformat(),"real_observations_only":True,
        "series_start":"2026-11-06T15:00:00-03:00",
        "stations":{},"errors":[]}
    day0=(target-dt.timedelta(days=1)).date().isoformat()
    day1=(target+dt.timedelta(days=1)).date().isoformat()
    for st in STATIONS:
        sid=st["id"]
        src=SOURCES.get(sid,{"status":"no_source"})
        entry={"station_name":st["name"],"source":src["name"] if "name" in src else None,
               "source_status":src["status"],"observed":None}
        result["stations"][sid]=entry
        if st.get("enabled",True) is False:
            entry["status"]="excluded";continue
        if sid!="inmet_a628_angra":
            entry["status"]="not_automated_no_verified_observation_endpoint"
            continue
        url=src["url_template"].format(start=day0,end=day1)
        entry["source_url"]=url
        try:
            http_status,content_type,body=request(url)
            entry["http_status"]=http_status
            entry["response_content_type"]=content_type
            entry["response_bytes"]=len(body)
            entry["raw_body_sample"]=body[:300].decode("utf-8",errors="replace") if body and "json" not in content_type.lower() else None
            if not body:
                entry["status"]="empty_api_response";continue
            try:
                payload=json.loads(body)
            except ValueError:
                entry["status"]="non_json_api_response";continue
            archive=ROOT/"data"/"observations"/"raw"/now.strftime("%Y-%m-%d")
            archive.mkdir(parents=True,exist_ok=True)
            fname=archive/("inmet_a628_"+now.strftime("%H%M%S")+".json")
            fname.write_text(json.dumps(payload,ensure_ascii=False)+"\n",encoding="utf-8")
            entry["raw_archive"]=str(fname.relative_to(ROOT))
            entry.update(normalize_inmet(payload,target))
        except urllib.error.HTTPError as e:
            entry["http_status"]=e.code;entry["status"]="http_error"
        except Exception as e:
            entry["status"]="retrieval_error";entry["error_type"]=type(e).__name__
    out=ROOT/"data"/"observations"/"reports"/now.strftime("%Y-%m-%d")
    out.mkdir(parents=True,exist_ok=True)
    path=out/(now.strftime("%H%M%S")+".json")
    path.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print("Observation report",path.relative_to(ROOT))
    for sid,r in result["stations"].items(): print(sid,r["status"])
    print("Actual observed stations:",sum(x.get("observed") is not None for x in result["stations"].values()))

if __name__=="__main__":
    main()
