"""Start early, wait locally and retry Meteoblue inside its original window."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import collect
from study_config import HORIZON_HOURS, ROOT, SLOT_HOURS, SLOT_TOLERANCE, TZ

EARLY_SCHEDULE_HOURS = {
    cron: hour for hour in SLOT_HOURS
    for cron in (f"47 {hour + 1} * * *", f"7 {hour + 2} * * *")
}
RETRY_SECONDS = 300


def nominal_issue(now, cron, manual_hour=None, created_at=None):
    hour = EARLY_SCHEDULE_HOURS.get(cron, manual_hour)
    if hour not in SLOT_HOURS:
        raise ValueError("Unknown early schedule or reference hour")
    # Keep the run's creation date if a queued runner crosses midnight.
    day = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00")).astimezone(TZ) if created_at else now
    return day.replace(hour=hour, minute=0, second=0, microsecond=0)


def targets_for(issue):
    return [(issue + dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in HORIZON_HOURS]


def run_window(issue, *, clock, sleep, refresh, completed, acquire, publish):
    """Only a successfully published, validated snapshot completes a slot."""
    opening, closing = issue - SLOT_TOLERANCE, issue + SLOT_TOLERANCE
    while clock() < opening:
        sleep(min(30, (opening - clock()).total_seconds()))
    while True:
        # Checkout/pull happens after the preceding attempt has been published.
        refresh()
        if completed():
            return "already_archived"
        if clock() > closing:
            return "missed_window"
        acquire()
        # This must succeed even for partial acquisitions before retrying.
        publish()
        if completed():
            return "complete_archived"
        remaining = (closing - clock()).total_seconds()
        if remaining <= 0:
            return "partial_window_closed"
        wake = clock() + dt.timedelta(seconds=min(RETRY_SECONDS, remaining))
        while clock() < wake:
            sleep(min(30, (wake - clock()).total_seconds()))


def git(*args, check=True):
    return subprocess.run(["git", *args], cwd=ROOT, check=check)


def publish_data():
    git("add", "data/")
    if git("diff", "--cached", "--quiet", check=False).returncode:
        git("commit", "-m", "Archive early Meteoblue attempt and diagnostics")
    for attempt in range(5):
        pulled = git("pull", "--rebase", "origin", "main", check=False)
        if pulled.returncode == 0 and git("push", "origin", "HEAD:main", check=False).returncode == 0:
            return
        if (ROOT / ".git/rebase-merge").exists() or (ROOT / ".git/rebase-apply").exists():
            git("rebase", "--abort")
        time.sleep(3 * (attempt + 1))
    raise RuntimeError("Archive push failed; preserve local data with workflow artifact")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot-hour", type=int, choices=SLOT_HOURS)
    args = parser.parse_args()
    issue = nominal_issue(dt.datetime.now(TZ), os.environ.get("GITHUB_EVENT_SCHEDULE", ""),
                          args.slot_hour, os.environ.get("GITHUB_RUN_CREATED_AT"))
    targets = targets_for(issue)
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    attempts = 0

    def acquire():
        nonlocal attempts
        attempts += 1
        env = dict(os.environ, FORECAST_EARLY_WORKER="1",
                   FORECAST_NOMINAL_ISSUE_AT_LOCAL=issue.isoformat(),
                   GITHUB_EVENT_NAME="workflow_dispatch")
        # A partial result intentionally exits nonzero and is still archived.
        result = subprocess.run([sys.executable, str(ROOT / "collect.py")], cwd=ROOT, env=env)
        print("Attempt", attempts, "collector exit", result.returncode, flush=True)

    status = run_window(
        issue, clock=lambda: dt.datetime.now(TZ), sleep=time.sleep,
        refresh=lambda: git("pull", "--rebase", "origin", "main"),
        completed=lambda: collect.meteoblue_capture_state(issue, targets)[0] is not None,
        acquire=acquire, publish=publish_data,
    )
    now = dt.datetime.now(TZ)
    collect.save_json_atomic(
        ROOT / "data/early_collection_diagnostics" / issue.strftime("%Y-%m-%d") /
        (f"{issue:%H%M}_{os.environ.get('GITHUB_RUN_ID', 'local')}_{now:%H%M%S%f}.json"),
        {"nominal_issue_at_local": issue.isoformat(), "finished_at_local": now.isoformat(),
         "status": status, "attempts": attempts,
         "schedule_event": os.environ.get("GITHUB_EVENT_SCHEDULE"),
         "github_run_id": os.environ.get("GITHUB_RUN_ID")},
    )
    publish_data()
    print("Early Meteoblue result:", status, flush=True)
    if status not in ("already_archived", "complete_archived"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
