#!/usr/bin/env python3
"""Audit coordinates, local dates and timezone consistency; never silently correct forecasts."""
import datetime as dt
import json
import math
import os
from pathlib import Path
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo("America/Sao_Paulo")
NITEROI = (-22.88333, -43.10361)
ANGRA = (-22.97555554, -44.30333333)

def distance_km(a, b):
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371*2*math.asin(min(1, math.sqrt(h)))

def get(url, params):
    req = urllib.request.Request(url+"?"+urllib.parse.urlencode(params),
                                 headers={"User-Agent":"RJSP-Meteo-Audit/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)

def check_point(label, requested, returned, max_grid_km=30):
    lat, lon = returned
    d = distance_km(requested, returned)
    return {"source":label,"requested":list(requested),"returned":list(returned),
            "distance_km":round(d,3),"valid_hemisphere":(-24.5<lat<-21 and -46<lon<-42),
            "within_grid_tolerance":d<=max_grid_km,
            "pass":(-24.5<lat<-21 and -46<lon<-42 and d<=max_grid_km)}

def check_times(label, times, target, offset_hours):
    expected = dt.datetime.fromisoformat(target).replace(tzinfo=TZ)
    utc = expected.astimezone(dt.timezone.utc)
    roundtrip = utc.astimezone(TZ)
    iso_date = dt.date.fromisoformat(target[:10])
    return {"source":label,"target_local":target,"target_utc":utc.isoformat(),
            "utc_offset_hours":offset_hours,"target_present":target in times,
            "date_iso_valid":iso_date.isoformat()==target[:10],
            "utc_roundtrip_valid":roundtrip==expected,
            "pass":target in times and roundtrip==expected and offset_hours==-3}

def main():
    folders=sorted((ROOT/"data"/"smoke_tests").glob("*/*/report.json"))
    if not folders:
        raise SystemExit("No archived smoke report found")
    path=folders[-1]
    report=json.loads(path.read_text(encoding="utf-8"))
    folder=path.parent
    openraw=json.loads((folder/"open_meteo_raw.json").read_text(encoding="utf-8"))
    basic=json.loads((folder/"meteoblue_basic-1h_raw.json").read_text(encoding="utf-8"))
    wind=json.loads((folder/"meteoblue_wind-3h_raw.json").read_text(encoding="utf-8"))
    requested=(report["coordinate"]["latitude"],report["coordinate"]["longitude"])
    checks=[]
    checks.append(check_point("Open-Meteo Angra",requested,(openraw["latitude"],openraw["longitude"])))
    for name,raw in (("Meteoblue basic Angra",basic),("Meteoblue wind Angra",wind)):
        m=raw["metadata"]
        checks.append(check_point(name,requested,(m["latitude"],m["longitude"])))
    assert openraw["timezone"]=="America/Sao_Paulo"
    assert openraw["utc_offset_seconds"]==-10800
    for raw in (basic,wind):
        assert raw["metadata"]["utc_timeoffset"]==-3
    for target in report["targets_local"]:
        checks.append(check_times("Open-Meteo Angra",openraw["hourly"]["time"],target,-3))
        checks.append(check_times("Meteoblue basic Angra",[x.replace(" ","T") for x in basic["data_1h"]["time"]],target,-3))
        checks.append(check_times("Meteoblue wind Angra",[x.replace(" ","T") for x in wind["data_3h"]["time"]],target,-3))
    # A live known-place control checks BOTH providers' coordinate notation.
    lat,lon=NITEROI
    try:
        o=get("https://api.open-meteo.com/v1/forecast",
              {"latitude":lat,"longitude":lon,"hourly":"wind_speed_10m",
               "models":"ncep_gfs_global,icon_global,ecmwf_ifs",
               "wind_speed_unit":"kn","timezone":"America/Sao_Paulo","forecast_days":2})
        checks.append(check_point("Open-Meteo Niteroi",NITEROI,(o["latitude"],o["longitude"])))
        checks.append({"source":"Open-Meteo Niteroi timezone","pass":o.get("timezone")=="America/Sao_Paulo" and o.get("utc_offset_seconds")==-10800})
    except Exception as e:
        checks.append({"source":"Open-Meteo Niteroi","pass":False,"error":type(e).__name__})
    key=os.getenv("METEOBLUE_API_KEY")
    if key:
        for package in ("basic-1h","wind-3h"):
            try:
                m=get("https://my.meteoblue.com/packages/"+package,
                      {"lat":lat,"lon":lon,"asl":10,"tz":"America/Sao_Paulo",
                       "format":"json","apikey":key})["metadata"]
                checks.append(check_point("Meteoblue "+package+" Niteroi",NITEROI,(m["latitude"],m["longitude"])))
                checks.append({"source":"Meteoblue "+package+" Niteroi timezone","pass":m.get("utc_timeoffset")==-3})
            except Exception as e:
                checks.append({"source":"Meteoblue "+package+" Niteroi","pass":False,"error":type(e).__name__})
    else:
        checks.append({"source":"Meteoblue Niteroi","pass":False,"error":"MissingSecret"})
    audit={"source_smoke_report":str(path.relative_to(ROOT)),
           "audit_at":dt.datetime.now(TZ).isoformat(),
           "reference_niteroi":{"latitude":lat,"longitude":lon},
           "checks":checks,"passed":all(x["pass"] for x in checks)}
    output=folder/"notation_audit.json"
    output.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Notation audit:",sum(x["pass"] for x in checks),"/",len(checks),
          "checks; passed:",audit["passed"],"output:",output)
    if not audit["passed"]:
        raise SystemExit(1)

if __name__=="__main__":
    main()
