#!/usr/bin/env python3
"""Probe official INMET WIS2 and legacy station feeds, without promoting unverified data."""
import datetime as dt
import json
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parent
now=dt.datetime.now(ZoneInfo("America/Sao_Paulo"))
urls={
 "wis2_stations":"https://wis2bra.inmet.gov.br/oapi/collections/stations/items?f=json&limit=5",
 "wis2_station_near_angra":"https://wis2bra.inmet.gov.br/oapi/collections/stations/items?"+urllib.parse.urlencode({"f":"json","limit":20,"bbox":"-44.5,-23.3,-44.0,-22.7"}),
 "wis2_synop":"https://wis2bra.inmet.gov.br/oapi/collections/urn:wmo:md:br-inmet:synop/items?f=json&limit=5",
 "wis2_synop_near_angra":"https://wis2bra.inmet.gov.br/oapi/collections/urn:wmo:md:br-inmet:synop/items?"+urllib.parse.urlencode({"f":"json","limit":20,"bbox":"-44.5,-23.3,-44.0,-22.7"}),
 "inmet_catalog":"https://apitempo.inmet.gov.br/estacoes/T",
 "inmet_legacy_observations":"https://apitempo.inmet.gov.br/estacao/2026-11-05/2026-11-07/A628"
}
result={"time_brt":now.isoformat(),"checks":{}}
folder=ROOT/"data"/"observation_probes"/now.strftime("%Y-%m-%d")/now.strftime("%H%M%S")
folder.mkdir(parents=True,exist_ok=True)
for label,url in urls.items():
 record={"url":url}
 try:
  req=urllib.request.Request(url,headers={"User-Agent":"RJSP-Meteo-Observations/1.0","Accept":"application/geo+json,application/json"})
  with urllib.request.urlopen(req,timeout=18) as resp:
   body=resp.read(250000)
   record.update({"http":resp.status,"content_type":resp.headers.get("Content-Type"),"bytes":len(body)})
  if body:
   try:
    j=json.loads(body)
    record["json_type"]=type(j).__name__
    if isinstance(j,dict):
     record["keys"]=list(j)[:20]
     record["feature_count"]=len(j.get("features",[]))
     record["returned_count"]=j.get("numberReturned")
     record["item_examples"]=[{"id":x.get("id"),"properties":x.get("properties"),"geometry":x.get("geometry")} for x in j.get("features",[])[:3]]
    elif isinstance(j,list):
     record["record_count"]=len(j)
     record["examples"]=j[:2]
    (folder/(label+".json")).write_text(json.dumps(j,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
   except ValueError:record["body_sample"]=body[:160].decode(errors="replace")
 except Exception as e:
  record["error"]=type(e).__name__
  if isinstance(e,urllib.error.HTTPError):record["http"]=e.code
 result["checks"][label]=record
(folder/"report.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print("Report",folder)
for k,v in result["checks"].items():print(k,v.get("http"),v.get("error"),v.get("feature_count"),v.get("record_count"))
