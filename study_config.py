"""Shared forecast and observation settings for the official study series."""
from bisect import bisect_right
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import os

CODE_ROOT = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("RJSP_DATA_ROOT", CODE_ROOT))
TZ = ZoneInfo("America/Sao_Paulo")
SLOT_HOURS = (9, 12, 15, 18)
SLOT_TOLERANCE = timedelta(minutes=30)
HORIZON_HOURS = (24, 48, 72)
SERIES_START = datetime(2026, 11, 7, 15, tzinfo=TZ)
FIRST_FULL_FRAME = SERIES_START + timedelta(hours=max(HORIZON_HOURS))
STATIONS = json.loads((CODE_ROOT / "stations.json").read_text(encoding="utf-8"))
_DIRECTION_OCTANTS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
_OCTANT_BOUNDARIES = (22.5, 67.5, 112.5, 157.5, 202.5, 247.5, 292.5, 337.5)


def direction_octant(degrees):
    """Classify a wind-from bearing, assigning boundaries clockwise."""
    if type(degrees) not in (int, float) or not 0 <= degrees <= 360:
        return None
    index = bisect_right(_OCTANT_BOUNDARIES, degrees)
    return _DIRECTION_OCTANTS[index % len(_DIRECTION_OCTANTS)]


def local_time(value):
    when = datetime.fromisoformat(value)
    return when.astimezone(TZ) if when.tzinfo else when.replace(tzinfo=TZ)


def within_slot(when, slot):
    return abs(when - slot) <= SLOT_TOLERANCE
