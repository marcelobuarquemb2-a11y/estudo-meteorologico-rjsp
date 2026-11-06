"""Official measured INMET WIS2 SYNOP readings at A628 Angra, strictly station/time matched."""
import datetime as dt
import json
import urllib.parse
import urllib.request
WIGOS="0-76-0-3300100000000555"
KNOTS=1.9438444924
BASE="https://wis2bra.inmet.gov.br/oapi/collections/urn:wmo:md:br-inmet:synop/items"
NAMES={"direction_deg":("wind_direction","deg"),"mean_kt":("wind_speed","m/s"),
       "gust_kt":("maximum_wind_gust_speed","m/s"),
       "precip_mm":("total_precipitation_or_total_water_equivalent","kg m-2")}
def acquire(target):
    """Return (verified observation metadata, original response); never infer absent readings."""
    target=target.astimezone(dt.timezone.utc)
    span_start=(target-dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    span_end=(target+dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params={"f":"json","bbox":"-44.35,-23.02,-44.25,-22.93",
            "datetime":span_start+"/"+span_end,"limit":200}
    url=BASE+"?"+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={"User-Agent":"RJSP-Real-Station-Observations/1.0","Accept":"application/geo+json,application/json"})
    with urllib.request.urlopen(req,timeout=30) as resp:
        data=json.load(resp)
    selected={}
    match_time=target.strftime("%Y-%m-%dT%H:%M:%SZ")
    for feature in data.get("features",[]):
        p=feature.get("properties",{})
        if p.get("wigos_station_identifier")!=WIGOS or p.get("reportTime")!=match_time:
            continue
        name=p.get("name")
        if name in selected:
            return {"status":"duplicate_measurement","target_utc":match_time,"source_url":url},data
        selected[name]=p
    vals={}
    for out_key,(name,expected) in NAMES.items():
        row=selected.get(name,{})
        value=row.get("value")
        if not isinstance(value,(float,int)) or row.get("units")!=expected:
            vals[out_key]=None
        elif out_key in ("mean_kt","gust_kt") and 0<=value<=100:
            vals[out_key]=round(value*KNOTS,3)
        elif out_key=="direction_deg" and 0<=value<=360:
            vals[out_key]=value
        elif out_key=="precip_mm" and 0<=value<=500:
            vals[out_key]=value
        else:
            vals[out_key]=None
    good=any(v is not None for v in vals.values())
    report={"status":"observation_available_wis2" if good else "target_not_available_wis2",
      "source_url":url,"station_wigos":WIGOS,"target_utc":match_time,
      "observed":vals if good else None,"features_returned":len(data.get("features",[])),
      "units":{key:selected.get(name,{}).get("units") for key,(name,_) in NAMES.items()},
      "report_time_matched":bool(selected),
      "precipitation_definition":"kg/m2 = mm of liquid water (hourly period must be checked)",
      "measurement_source":"INMET WIS2 SYNOP"}
    return report,data
