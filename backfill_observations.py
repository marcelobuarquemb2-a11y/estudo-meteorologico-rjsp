#!/usr/bin/env python3
"""Revisit yesterday's four slots to capture legitimately delayed station measurements."""
import datetime as dt
import os
import subprocess
import sys
from zoneinfo import ZoneInfo
yesterday=dt.datetime.now(ZoneInfo("America/Sao_Paulo")).date()-dt.timedelta(days=1)
for hour in (9,12,15,18):
    env=os.environ.copy()
    env["OBS_TARGET_BRT"]=f"{yesterday.isoformat()}T{hour:02d}:00"
    print("Backfill measured station observations:",env["OBS_TARGET_BRT"],flush=True)
    subprocess.run([sys.executable,"collect_observations.py"],env=env,check=True)
