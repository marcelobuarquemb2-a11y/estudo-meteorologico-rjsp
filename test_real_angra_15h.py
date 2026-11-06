#!/usr/bin/env python3
"""Verify availability of 15h BRT real A628 station reading for official first date."""
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from inmet_wis2 import acquire
ROOT=Path(__file__).resolve().parent
TARGET=dt.datetime(2026,11,6,15,0,tzinfo=ZoneInfo("America/Sao_Paulo"))
report,raw=acquire(TARGET)
folder=ROOT/"data"/"observation_probes"/"official_15h_2026-11-06"
folder.mkdir(parents=True,exist_ok=True)
(folder/"raw.json").write_text(json.dumps(raw,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
(folder/"report.json").write_text(json.dumps({"target_brt":TARGET.isoformat(),"result":report},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print("Status:",report["status"],"values:",report.get("observed"))
