"""Shared ten-minute acquisition cadence around the study's nominal hours."""
from study_config import SLOT_HOURS

ATTEMPT_OFFSETS_MINUTES = (-20, -10, 0, 10, 20, 30)
SCHEDULE_HOURS = {
    cron: hour
    for hour in SLOT_HOURS
    for cron in (f"40,50 {hour + 2} * * *", f"0,10,20,30 {hour + 3} * * *")
}


def needs_query(now, nominal, complete, nominal_attempted):
    """Always sample before nominal; require one refresh at/after nominal."""
    return now < nominal or not complete or not nominal_attempted
