"""Shared forecast and observation settings for the official study series."""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo("America/Sao_Paulo")
SLOT_HOURS = (9, 12, 15, 18)
HORIZON_HOURS = (24, 48, 72)
SERIES_START = datetime(2026, 11, 7, 15, tzinfo=TZ)
FIRST_FULL_FRAME = SERIES_START + timedelta(hours=max(HORIZON_HOURS))
STATIONS = json.loads((ROOT / "stations.json").read_text(encoding="utf-8"))
