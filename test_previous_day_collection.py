"""Offline checks of daily historical acquisition and lossless retries."""
import copy
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import Mock, patch

import collect_previous_day as daily
import recover_forecast_history as recovery
from study_config import STATIONS, TZ


class PreviousDayTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.day = date(2026, 11, 15)
        self.now = datetime(2026, 11, 16, 9, tzinfo=TZ)
        self.enterContext(patch.dict(os.environ, {}, clear=True))

    def response(self, params):
        run = datetime.fromisoformat(params["run"])
        values = []
        for station in STATIONS:
            times = []
            hourly = {field: [] for field in daily.FIELDS.values()}
            for hour in (9, 12, 15, 18):
                issue = datetime.combine(run.date(), datetime.min.time(), tzinfo=TZ).replace(hour=hour)
                for target in daily.targets(issue):
                    times.append(target)
                    for name, field in daily.FIELDS.items():
                        hourly[field].append(90 if name == "direction_deg" else 0)
            values.append({"latitude": station["lat"], "longitude": station["lon"],
                           "utc_offset_seconds": -10800,
                           "hourly": {"time": times, **hourly},
                           "hourly_units": {field: daily.UNITS[name] for name, field in daily.FIELDS.items()}})
        return values

    def collect(self, fetcher=None, **kwargs):
        return daily.collect_day(self.day, root=self.root, now=self.now,
                                 fetcher=fetcher or self.response, originals=kwargs.pop("originals", {}), **kwargs)

    def test_schedule_has_ten_twenty_minute_opportunities_in_brasilia(self):
        path = Path(__file__).parent / ".github/workflows/coleta_retroativa_diaria.yml"
        crons = re.findall(r"cron: '([^']+)'", path.read_text())
        minutes = sorted((int(hour) - 3) * 60 + int(minute)
                         for cron in crons for minute in cron.split()[0].split(",")
                         for hour in cron.split()[1].split(","))
        self.assertEqual(minutes, list(range(9 * 60, 12 * 60 + 1, 20)))

    def test_complete_day_has_864_unique_values_and_no_meteoblue(self):
        fetcher = Mock(side_effect=self.response)
        report = self.collect(fetcher)
        self.assertEqual(fetcher.call_count, 12)
        self.assertEqual(report["forecast_values_present"], 864)
        self.assertEqual(report["forecast_values_expected"], 864)
        self.assertTrue(report["complete"])
        self.assertEqual(report["attempt_count"], 1)
        for path in report["archives"]:
            saved = daily.read(self.root / path)
            self.assertFalse(saved["official_series_eligible"])
            self.assertTrue(saved["accepted_for_study"])
            for station in saved["station_results"].values():
                self.assertEqual(len(station["forecasts"]), 9)
                self.assertEqual({r["model"] for r in station["forecasts"]}, set(daily.MODELS))

    def test_completed_day_skips_all_calls_and_does_not_rewrite_status(self):
        self.collect()
        path = self.root / "data/daily_recovery/2026-11-15/status.json"
        before = path.read_bytes()
        fetcher = Mock(side_effect=AssertionError("must not query"))
        report = self.collect(fetcher)
        fetcher.assert_not_called()
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(report["_changed"])

    def test_partial_day_retries_only_missing_model_slot_and_preserves_values(self):
        def partial(params):
            response = self.response(params)
            if params["models"] == "icon_global":
                # 09h and 12h use the same 06Z run. Only one valid hour is absent.
                for report in response:
                    i = report["hourly"]["time"].index("2026-11-16T09:00")
                    report["hourly"]["wind_gusts_10m"][i] = None
            return response
        first = self.collect(partial)
        self.assertEqual(first["forecast_values_present"], 858)
        issue = datetime(2026, 11, 15, 9, tzinfo=TZ)
        saved_before = daily.read(daily.archive_path(self.root, issue))

        def changed(params):
            response = self.response(params)
            for report in response:
                report["hourly"]["wind_speed_10m"] = [50] * len(report["hourly"]["time"])
            return response
        fetcher = Mock(side_effect=changed)
        second = self.collect(fetcher)
        self.assertTrue(second["complete"])
        self.assertEqual(second["attempt_count"], 2)
        self.assertEqual(fetcher.call_count, 1)
        after = daily.read(daily.archive_path(self.root, issue))
        sid = STATIONS[0]["id"]
        self.assertEqual(saved_before["station_results"][sid]["forecasts"][0]["values"]["mean_kt"], 0)
        self.assertEqual(after["station_results"][sid]["forecasts"][0]["values"]["mean_kt"], 0)

    def test_provider_failure_is_recorded_and_other_models_succeed(self):
        def failed(params):
            if params["models"] == "ncep_gfs_global":
                raise TimeoutError("temporarily unavailable")
            return self.response(params)
        report = self.collect(failed)
        self.assertEqual(report["forecast_values_present"], 576)
        errors = [g for g in report["attempts"][0]["groups"] if g["model"] == "GFS"]
        self.assertEqual(len(errors), 4)
        self.assertTrue(all(g["errors"][0]["error"] == "TimeoutError" for g in errors))

    def test_ten_failed_attempts_are_capped_without_more_calls(self):
        fetcher = Mock(side_effect=TimeoutError())
        for _ in range(10):
            report = self.collect(fetcher)
        self.assertEqual(report["attempt_count"], 10)
        self.assertEqual(report["status"], "attempts_exhausted")
        fetcher.reset_mock()
        report = self.collect(fetcher)
        fetcher.assert_not_called()
        self.assertFalse(report["complete"])

    def test_same_github_run_does_not_consume_an_extra_attempt(self):
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "run-123"}):
            self.collect(Mock(side_effect=TimeoutError()))
            fetcher = Mock()
            report = self.collect(fetcher)
        fetcher.assert_not_called()
        self.assertEqual(report["attempt_count"], 1)

    def test_current_or_future_reference_date_is_rejected(self):
        for reference in (self.now.date(), self.now.date() + timedelta(days=1)):
            with self.assertRaises(ValueError):
                daily.collect_day(reference, root=self.root, now=self.now)

    def test_wrong_units_are_not_counted_and_zero_is_a_valid_measurement(self):
        params = daily.request_params(datetime(2026, 11, 15, 9, tzinfo=TZ), "ICON", "2026-11-15T06:00", STATIONS)
        response = self.response(params)
        response[0]["hourly_units"]["wind_speed_10m"] = "km/h"
        result = daily.extract(response, datetime(2026, 11, 15, 9, tzinfo=TZ), "ICON", STATIONS)
        row = result[STATIONS[0]["id"]]["forecasts"][0]
        self.assertIsNone(row["values"]["mean_kt"])
        self.assertEqual(row["values"]["gust_kt"], 0)

    def test_invalid_grid_offset_or_duplicate_times_are_rejected(self):
        issue = datetime(2026, 11, 15, 9, tzinfo=TZ)
        params = daily.request_params(issue, "ICON", "2026-11-15T06:00", STATIONS)
        for kind in ("grid", "offset", "duplicate"):
            with self.subTest(kind=kind):
                response = self.response(params)
                if kind == "grid": response[0]["latitude"] = 0
                if kind == "offset": response[0]["utc_offset_seconds"] = 0
                if kind == "duplicate": response[0]["hourly"]["time"].append(response[0]["hourly"]["time"][0])
                with self.assertRaises(ValueError):
                    daily.extract(response, issue, "ICON", STATIONS)

    def test_snapshot_correspondence_selects_other_run_without_using_actuals(self):
        issue = datetime(2026, 11, 15, 18, tzinfo=TZ)
        archive = daily.empty_archive(issue, STATIONS)
        for station in archive["station_results"].values():
            for row in station["forecasts"]:
                if row["model"] == "ICON":
                    row["values"] = {"direction_deg": 180, "mean_kt": 1, "gust_kt": 1, "precip_mm": 1}
        original = (None, issue, "original.json", archive["station_results"])
        def snapshots(params):
            response = self.response(params)
            if params["run"] == "2026-11-15T18:00":
                for report in response:
                    for name, field in daily.FIELDS.items():
                        report["hourly"][field] = [180 if name == "direction_deg" else 1] * len(report["hourly"]["time"])
            return response
        selected, evidence = daily.select_group(issue, "ICON", STATIONS, original,
                                                daily.empty_archive(issue, STATIONS), snapshots)
        self.assertEqual(selected[0], "2026-11-15T18:00")
        self.assertEqual(evidence["snapshot_matches"], 72)
        self.assertEqual(evidence["snapshot_differences"], 0)

    def test_archives_are_compatible_with_recovery_aware_pair_builder(self):
        report = self.collect()
        with patch.object(recovery, "ROOT", self.root), \
             patch.object(recovery, "RECOVERED", self.root / "data/recovered_forecasts"), \
             patch.object(recovery, "OUTPUT", self.root / "data/comparisons/recovered_pairs_status.json"), \
             patch.object(recovery, "forecast_archives", return_value={}), \
             patch.object(recovery, "measured_by_time", return_value={}):
            recovery.build_pairs()
        paired = daily.read(self.root / "data/comparisons/recovered_pairs_status.json")
        self.assertEqual(len(paired["by_slot"]), 4)
        self.assertEqual(paired["summary"]["available_pairs"], 0)
        self.assertTrue(report["complete"])

    def test_realtime_sources_remain_untouched(self):
        original = self.root / "data/2026-11/2026-11-15/090000.json"
        original.parent.mkdir(parents=True)
        original.write_text('{"original":true}')
        self.collect()
        self.assertEqual(original.read_text(), '{"original":true}')


if __name__ == "__main__":
    unittest.main()
