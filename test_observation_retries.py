"""Offline acquisition checks: early readings, nominal refresh and missing-source retries."""
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

import collect_observations as observations
import compare_forecasts
from collection_schedule import SCHEDULE_HOURS
from study_config import SLOT_HOURS, TZ


class FrozenDateTime(dt.datetime):
    current = dt.datetime(2026, 11, 11, 9, tzinfo=TZ)

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


class ObservationRetryTests(unittest.TestCase):
    def setUp(self):
        self.target = dt.datetime(2026, 11, 11, 9, tzinfo=TZ)
        FrozenDateTime.current = self.target
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.enterContext(patch.object(observations, "ROOT", self.root))
        self.enterContext(patch.object(observations, "OBSERVATIONS", self.root / "data" / "observations"))
        self.enterContext(patch.object(observations, "datetime", FrozenDateTime))
        self.enterContext(patch.dict(observations.os.environ, {
            "GITHUB_EVENT_SCHEDULE": "0,10,20,30 12 * * *",
        }, clear=True))
        self.enterContext(redirect_stdout(io.StringIO()))
        self.values = {"direction_deg": 100, "mean_kt": 3, "gust_kt": 5, "precip_mm": 0}

    def record(self, target, acquired):
        measured = min(target, acquired)
        return {"observed_at_local": measured.isoformat(), "observed": dict(self.values)}

    def wis2(self, targets, raw_dir, available_at=None):
        return {
            sid: {"provider": "INMET WIS2 SYNOP", "records": [self.record(targets[0], available_at)], "requests": []}
            for sid in observations.INMET
        }

    def sma(self, sid, config, targets, raw_dir, available_at=None):
        return {"provider": "PortosRio SMA", "records": [self.record(targets[0], available_at)], "requests": []}

    def run_capture(self, minutes, wis2=None, sma=None):
        FrozenDateTime.current = self.target + dt.timedelta(minutes=minutes)
        with patch.object(observations, "collect_wis2", side_effect=wis2 or self.wis2) as wis:
            with patch.object(observations, "collect_sma", side_effect=sma or self.sma) as port:
                observations.main()
        return wis, port

    def reports(self):
        return sorted((observations.OBSERVATIONS / "reports").glob("*/*.json"))

    def test_primary_schedule_matches_forecasts_and_keeps_delayed_recovery(self):
        workflow = Path(__file__).parent / ".github" / "workflows" / "observacoes.yml"
        crons = re.findall(r"cron: '([^']+)'", workflow.read_text(encoding="utf-8"))
        offsets = {hour: [] for hour in SLOT_HOURS}
        for cron in crons:
            if cron not in SCHEDULE_HOURS:
                continue
            minute, utc_hour, *_ = cron.split()
            target = SCHEDULE_HOURS[cron]
            offsets[target].extend((int(utc_hour) - 3 - target) * 60 + int(m) for m in minute.split(","))
        for hour, minutes in offsets.items():
            self.assertEqual(sorted(minutes), [-20, -10, 0, 10, 20, 30])
            for delay_hour in (hour + 4, hour + 5):
                self.assertIn(f"20 {delay_hour} * * *", crons)
        self.assertIn("40 11 * * *", crons)

    def test_pre_success_still_refreshes_at_minus_ten_and_nominal_then_stops(self):
        for minutes in (-20, -10, 0):
            wis, port = self.run_capture(minutes)
            wis.assert_called_once()
            self.assertEqual(port.call_count, len(observations.PORTOSRIO))
        wis, port = self.run_capture(10)
        wis.assert_not_called()
        port.assert_not_called()
        self.assertEqual(len(self.reports()), 3)
        with patch.object(compare_forecasts, "ROOT", self.root):
            selected = compare_forecasts.measured_by_time({s["id"] for s in observations.ACTIVE}, [])
        for _, source, values, measured in selected[self.target].values():
            self.assertEqual(measured, self.target)
            self.assertEqual(values["precip_mm"], 0)

    def test_failed_nominal_refresh_preserves_earlier_complete_readings(self):
        self.run_capture(-20)

        def unavailable_sma(*args, **kwargs):
            return {"records": [], "errors": [{"error": "TimeoutError"}]}

        self.run_capture(0, wis2=lambda *args, **kwargs: {}, sma=unavailable_sma)
        wis, port = self.run_capture(10)
        wis.assert_not_called()
        port.assert_not_called()
        saved, nominal_attempted = observations.archived_observations(self.target, FrozenDateTime.current)
        self.assertEqual(len(saved), len(observations.ACTIVE))
        self.assertTrue(nominal_attempted)

    def test_missing_rain_retries_inmet_only_and_preserves_complete_stations(self):
        missing_sid = next(iter(observations.INMET))

        def partial(*args, **kwargs):
            rows = self.wis2(*args, **kwargs)
            rows[missing_sid]["records"][0]["observed"]["precip_mm"] = None
            return rows

        self.run_capture(0, wis2=partial)
        first = self.reports()[0]
        first_bytes = first.read_bytes()
        wis, port = self.run_capture(10)
        wis.assert_called_once()
        port.assert_not_called()
        last = json.loads(self.reports()[-1].read_text())
        for sid in observations.PORTOSRIO:
            self.assertEqual(last["stations"][sid]["reused_from"], str(first.relative_to(self.root)))
        self.assertEqual(first.read_bytes(), first_bytes)
        wis, port = self.run_capture(20)
        wis.assert_not_called()
        port.assert_not_called()

    def test_missing_portosrio_station_does_not_requery_other_sources(self):
        missing_sid = next(iter(observations.PORTOSRIO))

        def partial(sid, *args, **kwargs):
            rows = self.sma(sid, *args, **kwargs)
            if sid == missing_sid:
                rows["records"][0]["observed"]["gust_kt"] = None
            return rows

        self.run_capture(0, sma=partial)
        wis, port = self.run_capture(10)
        wis.assert_not_called()
        port.assert_called_once()
        self.assertEqual(port.call_args.args[0], missing_sid)

    def test_first_delayed_post_nominal_attempt_refreshes_once(self):
        self.run_capture(-20)
        wis, port = self.run_capture(12)
        wis.assert_called_once()
        self.assertEqual(port.call_count, len(observations.PORTOSRIO))
        wis, port = self.run_capture(20)
        wis.assert_not_called()
        port.assert_not_called()

    def test_expired_primary_attempt_recovers_original_measurement(self):
        wis, port = self.run_capture(56)
        wis.assert_called_once()
        self.assertEqual(wis.call_args.args[0], [self.target])
        report = json.loads(self.reports()[-1].read_text())
        self.assertEqual(report["acquisition_phase"], "delayed_recovery")
        self.assertEqual(report["coverage"], {"present": 24, "expected": 24})

    def test_last_boundary_attempt_is_allowed(self):
        wis, port = self.run_capture(30)
        wis.assert_called_once()
        self.assertEqual(port.call_count, len(observations.PORTOSRIO))

    def test_delayed_recovery_is_conditional_and_accepts_late_publication(self):
        with patch.dict(observations.os.environ, {"GITHUB_EVENT_SCHEDULE": "20 13 * * *"}):
            wis, port = self.run_capture(80)
            wis.assert_called_once()
        with patch.dict(observations.os.environ, {"GITHUB_EVENT_SCHEDULE": "20 14 * * *"}):
            wis, port = self.run_capture(140)
            wis.assert_not_called()
            port.assert_not_called()

    def test_missing_values_do_not_become_zero_and_invalid_archives_do_not_stop_retries(self):
        self.run_capture(0)
        path = self.reports()[0]
        original = json.loads(path.read_text())
        sid = next(iter(original["stations"]))
        for invalid in (None, True, float("nan"), -1):
            report = copy.deepcopy(original)
            report["stations"][sid]["observed"]["precip_mm"] = invalid
            path.write_text(json.dumps(report))
            saved, _ = observations.archived_observations(self.target, self.target)
            self.assertNotIn(sid, saved)
        path.write_text("{")
        saved, _ = observations.archived_observations(self.target, self.target)
        self.assertEqual(saved, {})

    def test_best_observation_rejects_future_measurements(self):
        now = self.target - dt.timedelta(minutes=20)
        records = [self.record(self.target, self.target), self.record(self.target, now)]
        chosen = observations.best_observation(records, self.target, now)
        self.assertEqual(chosen["observed_at_local"], now.isoformat())
        empty = observations.best_observation(records[:1], self.target, now)
        self.assertTrue(all(value is None for value in empty["observed"].values()))

    def test_wis2_early_acquisition_excludes_future_station_rows(self):
        now = self.target - dt.timedelta(minutes=20)
        sid = next(iter(observations.INMET))
        _, wigos, lat, lon = observations.INMET[sid]
        features = []
        for measured in (now, self.target):
            for field, (name, unit, maximum, minutes) in observations.WIS_FIELDS.items():
                began = measured - dt.timedelta(minutes=minutes)
                features.append({"geometry": {"coordinates": [lon, lat]}, "properties": {
                    "wigos_station_identifier": wigos, "name": name, "units": unit,
                    "phenomenonTime": observations.as_utc(began) + "/" + observations.as_utc(measured),
                    "value": 0,
                }})
        with patch.object(observations, "fetch_json", return_value=({"type": "FeatureCollection", "features": features}, {})):
            rows = observations.collect_wis2([self.target], self.root, available_at=now)
        chosen = rows[sid]["records"][0]
        self.assertEqual(chosen["observed_at_local"], now.isoformat())
        self.assertTrue(all(value == 0 for value in chosen["observed"].values()))

    def test_sma_early_acquisition_keeps_closest_past_reading(self):
        now = self.target - dt.timedelta(minutes=20)
        sid, config = next(iter(observations.PORTOSRIO.items()))
        indexed = {}
        for offset in range(12):
            measured = now - dt.timedelta(minutes=offset * 5)
            for field in observations.SMA_FIELDS:
                indexed[field, measured] = [0]
        for field in observations.SMA_FIELDS:
            indexed[field, self.target] = [0]
        with patch.object(observations, "fetch_json", return_value=({}, {})):
            with patch.object(observations, "sma_index", return_value=indexed):
                rows = observations.collect_sma(sid, config, [self.target], self.root, available_at=now)
        chosen = rows["records"][0]
        self.assertEqual(chosen["observed_at_local"], now.isoformat())
        self.assertTrue(all(value == 0 for value in chosen["observed"].values()))


if __name__ == "__main__":
    unittest.main()
