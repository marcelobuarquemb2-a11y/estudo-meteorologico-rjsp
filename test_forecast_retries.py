"""Offline regression checks for scheduled forecast retries."""
import copy
import datetime as dt
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import collect
from compare_forecasts import MODEL_NAMES
from normalize_collection import FIELDS
from study_config import HORIZON_HOURS, SERIES_START, SLOT_HOURS, STATIONS, TZ


class FrozenDateTime(dt.datetime):
    current = dt.datetime(2026, 11, 11, 9, tzinfo=TZ)

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


class ForecastRetryTests(unittest.TestCase):
    def setUp(self):
        self.issue = dt.datetime(2026, 11, 11, 9, tzinfo=TZ)
        FrozenDateTime.current = self.issue
        self.targets = [
            (self.issue + dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M")
            for h in HORIZON_HOURS
        ]
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.enterContext(patch.object(collect, "ROOT", self.root))
        self.enterContext(patch.object(collect.dt, "datetime", FrozenDateTime))
        self.enterContext(patch.dict(collect.os.environ, {
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_EVENT_SCHEDULE": "40,50 11 * * *",
            "METEOBLUE_API_KEY": "offline-test",
        }, clear=True))
        self.enterContext(redirect_stdout(io.StringIO()))
        self.report = {
            "official_series_eligible": True,
            "official_series_start_local": SERIES_START.isoformat(),
            "nominal_issue_at_local": self.issue.isoformat(),
            "consulted_at": self.issue.isoformat(),
            "station_results": {},
        }
        for station in STATIONS:
            self.report["station_results"][station["id"]] = {
                "lat": station["lat"], "lon": station["lon"],
                "notation_audit": {"passed": True},
                "forecasts": [
                    {"model": model, "target_local": target, "valid_at_local": target,
                     "values": {field: 0 for field in FIELDS}}
                    for model in MODEL_NAMES for target in self.targets
                ],
            }

    def save_report(self, name, report):
        folder = self.root / "data" / "2026-11" / "2026-11-11"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def response(self, endpoint, params):
        metadata = {"utc_timeoffset": -3}
        times = [target.replace("T", " ") for target in self.targets]
        if "open-meteo" in endpoint:
            hourly = {"time": self.targets}
            units = {}
            for model in collect.MODELS:
                for field in collect.VARS:
                    key = field + "_" + model
                    hourly[key] = [0, 0, 0]
                    units[key] = "°" if field == "wind_direction_10m" else (
                        "mm" if field == "precipitation" else "kn"
                    )
            return {"latitude": params["latitude"], "longitude": params["longitude"],
                    "timezone": "America/Sao_Paulo", "utc_offset_seconds": -10800,
                    "hourly": hourly, "hourly_units": units}
        if endpoint.endswith("basic-1h"):
            return {"metadata": metadata,
                    "units": {"windspeed": "ms-1", "winddirection": "degree", "precipitation": "mm"},
                    "data_1h": {"time": times, "windspeed": [0, 0, 0],
                                "winddirection": [0, 0, 0], "precipitation": [0, 0, 0]}}
        return {"metadata": metadata, "units": {"windspeed": "ms-1"},
                "data_3h": {"time": times, "gust": [0, 0, 0]}}

    def test_schedule_covers_each_slot_and_retains_legacy_events(self):
        workflow = Path(__file__).with_name(".github") / "workflows" / "coleta.yml"
        crons = re.findall(r"cron: '([^']+)'", workflow.read_text(encoding="utf-8"))
        offsets = {hour: [] for hour in SLOT_HOURS}
        for cron in crons:
            minute, hour, *_ = cron.split()
            slot = collect.SCHEDULE_HOURS[cron]
            offsets[slot].extend((int(hour) - 3 - slot) * 60 + int(m) for m in minute.split(","))
        self.assertEqual(set(offsets), set(SLOT_HOURS))
        for hour, minutes in offsets.items():
            self.assertEqual(sorted(minutes), [-20, -10, 0, 10, 20, 30])
            self.assertEqual(collect.SCHEDULE_HOURS[f"45 {hour + 2} * * *"], hour)

    def test_complete_capture_skips_every_source_call(self):
        existing = self.save_report("090000.json", self.report)
        with patch.object(collect, "fetch") as fetch:
            collect.main()
        fetch.assert_not_called()
        self.assertEqual(collect.completed_capture(self.issue, self.targets), existing)

    def test_delayed_scheduled_attempt_skips_every_source_call(self):
        FrozenDateTime.current = self.issue + dt.timedelta(minutes=31)
        with patch.object(collect, "fetch") as fetch:
            with self.assertRaises(SystemExit):
                collect.main()
        fetch.assert_not_called()
        self.assertFalse((self.root / "data").exists())

    def test_late_attempt_is_harmless_after_a_complete_capture(self):
        self.save_report("090000.json", self.report)
        FrozenDateTime.current = self.issue + dt.timedelta(minutes=55)
        with patch.object(collect, "fetch") as fetch:
            collect.main()
        fetch.assert_not_called()

    def test_incomplete_or_invalid_capture_does_not_suppress_retry(self):
        for field in FIELDS:
            for value in (None, float("nan"), True):
                with self.subTest(field=field, value=value):
                    report = copy.deepcopy(self.report)
                    station = next(iter(report["station_results"].values()))
                    station["forecasts"][0]["values"][field] = value
                    self.save_report("090000.json", report)
                    self.assertIsNone(collect.completed_capture(self.issue, self.targets))
        report = copy.deepcopy(self.report)
        station = next(iter(report["station_results"].values()))
        station["forecasts"][0] = copy.deepcopy(station["forecasts"][1])
        self.save_report("090000.json", report)
        self.assertIsNone(collect.completed_capture(self.issue, self.targets))

    def test_capture_must_match_issue_station_and_notation_checks(self):
        changes = [
            lambda r: r.update(official_series_eligible=False),
            lambda r: r.update(nominal_issue_at_local=(self.issue - dt.timedelta(days=1)).isoformat()),
            lambda r: r.update(consulted_at=(self.issue + dt.timedelta(minutes=31)).isoformat()),
            lambda r: next(iter(r["station_results"].values())).update(lat=0),
            lambda r: next(iter(r["station_results"].values())).update(notation_audit={"passed": False}),
        ]
        for change in changes:
            report = copy.deepcopy(self.report)
            change(report)
            self.save_report("090000.json", report)
            self.assertIsNone(collect.completed_capture(self.issue, self.targets))

    def test_boundaries_are_inclusive_and_legacy_rows_are_supported(self):
        for minutes in (-30, 30):
            report = copy.deepcopy(self.report)
            report["consulted_at"] = (self.issue + dt.timedelta(minutes=minutes)).isoformat()
            for station in report["station_results"].values():
                for row in station["forecasts"]:
                    row.pop("valid_at_local")
            self.assertTrue(collect.collection_complete(report, self.issue, self.targets))
        report = copy.deepcopy(self.report)
        row = next(iter(report["station_results"].values()))["forecasts"][0]
        target = dt.datetime.fromisoformat(row["target_local"])
        row["valid_at_local"] = (target + dt.timedelta(minutes=31)).isoformat()
        self.assertFalse(collect.collection_complete(report, self.issue, self.targets))

    def test_unreadable_archive_does_not_block_valid_capture(self):
        bad = self.save_report("085900.json", {})
        bad.write_text("{", encoding="utf-8")
        good = self.save_report("090000.json", self.report)
        self.assertEqual(collect.completed_capture(self.issue, self.targets), good)

    def test_partial_capture_is_archived_then_retried_then_skipped(self):
        def partial(endpoint, params):
            if endpoint.endswith("wind-3h") and params["lat"] == STATIONS[0]["lat"]:
                raise TimeoutError
            return self.response(endpoint, params)

        with patch.object(collect, "fetch", side_effect=partial):
            with self.assertRaises(SystemExit):
                collect.main()
        first = self.root / "data" / "2026-11" / "2026-11-11" / "090000.json"
        self.assertTrue(first.is_file())
        first_bytes = first.read_bytes()
        self.assertIsNone(collect.completed_capture(self.issue, self.targets))
        FrozenDateTime.current = self.issue + dt.timedelta(minutes=11)
        with patch.object(collect, "fetch", side_effect=self.response) as fetch:
            collect.main()
        self.assertEqual(fetch.call_count, len(STATIONS) * 3)
        complete = collect.completed_capture(self.issue, self.targets)
        saved = json.loads(complete.read_text(encoding="utf-8"))
        self.assertEqual(saved["collection_summary"]["forecast_values_present"], 288)
        self.assertEqual(first.read_bytes(), first_bytes)
        FrozenDateTime.current = self.issue + dt.timedelta(minutes=21)
        with patch.object(collect, "fetch") as fetch:
            collect.main()
        fetch.assert_not_called()
        self.assertEqual(len(list(first.parent.glob("*.json"))), 2)

    def test_pre_nominal_success_still_queries_minus_ten_and_nominal(self):
        self.report["consulted_at"] = (self.issue - dt.timedelta(minutes=20)).isoformat()
        self.save_report("084000.json", self.report)
        for minutes in (-10, 0):
            FrozenDateTime.current = self.issue + dt.timedelta(minutes=minutes)
            with patch.object(collect, "fetch", side_effect=self.response) as fetch:
                collect.main()
            self.assertEqual(fetch.call_count, len(STATIONS) * 3)
        FrozenDateTime.current = self.issue + dt.timedelta(minutes=10)
        with patch.object(collect, "fetch") as fetch:
            collect.main()
        fetch.assert_not_called()

    def test_failed_nominal_refresh_keeps_earlier_complete_fallback(self):
        self.report["consulted_at"] = (self.issue - dt.timedelta(minutes=20)).isoformat()
        previous = self.save_report("084000.json", self.report)
        with patch.object(collect, "fetch", side_effect=TimeoutError):
            collect.main()
        self.assertEqual(collect.completed_capture(self.issue, self.targets), previous)
        FrozenDateTime.current = self.issue + dt.timedelta(minutes=10)
        with patch.object(collect, "fetch") as fetch:
            collect.main()
        fetch.assert_not_called()

    def test_delayed_first_post_nominal_attempt_still_refreshes(self):
        self.report["consulted_at"] = (self.issue - dt.timedelta(minutes=20)).isoformat()
        self.save_report("084000.json", self.report)
        FrozenDateTime.current = self.issue + dt.timedelta(minutes=12)
        with patch.object(collect, "fetch", side_effect=self.response) as fetch:
            collect.main()
        self.assertEqual(fetch.call_count, len(STATIONS) * 3)
        FrozenDateTime.current = self.issue + dt.timedelta(minutes=20)
        with patch.object(collect, "fetch") as fetch:
            collect.main()
        fetch.assert_not_called()

    def test_last_boundary_attempt_runs_but_later_start_does_not(self):
        FrozenDateTime.current = self.issue + dt.timedelta(minutes=30)
        with patch.object(collect, "fetch", side_effect=self.response) as fetch:
            collect.main()
        self.assertEqual(fetch.call_count, len(STATIONS) * 3)

    def test_deployment_does_not_make_unscheduled_source_calls(self):
        with patch.dict(collect.os.environ, {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_SCHEDULE": ""}):
            with patch.object(collect, "fetch") as fetch:
                collect.main()
        fetch.assert_not_called()

    def test_comparison_uses_complete_capture_closest_to_nominal(self):
        import compare_forecasts
        for minutes, filename in ((-20, "084000.json"), (-10, "085000.json"), (0, "090000.json")):
            report = copy.deepcopy(self.report)
            report["consulted_at"] = (self.issue + dt.timedelta(minutes=minutes)).isoformat()
            report["collection_summary"] = {"forecast_values_present": 288}
            self.save_report(filename, report)
        later = copy.deepcopy(self.report)
        later["consulted_at"] = (self.issue + dt.timedelta(minutes=10)).isoformat()
        later["collection_summary"] = {"forecast_values_present": 287}
        self.save_report("091000.json", later)
        with patch.object(compare_forecasts, "ROOT", self.root):
            chosen = compare_forecasts.forecast_archives([])[self.issue]
        self.assertEqual(chosen[1], self.issue)
        self.assertTrue(chosen[2].endswith("090000.json"))


if __name__ == "__main__":
    unittest.main()

