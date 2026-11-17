"""Offline checks for early start, delayed runner, retries and duplicate suppression."""
import copy
import datetime as dt
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import collect
import meteoblue_early as early
import test_forecast_retries as legacy
from study_config import SLOT_HOURS, TZ


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.issue = dt.datetime(2026, 11, 18, 9, tzinfo=TZ)
        self.now = self.issue - dt.timedelta(minutes=73)
        self.calls = []
        self.saved = False
        self.attempts = 0

    def sleep(self, seconds):
        self.assertLessEqual(seconds, 30)
        self.now += dt.timedelta(seconds=seconds)

    def acquire(self):
        self.calls.append(('query', self.now))
        self.attempts += 1

    def publish(self):
        self.calls.append(('publish', self.now))
        self.saved = True

    def run_worker(self, complete=None, publish=None):
        return early.run_window(self.issue, clock=lambda: self.now, sleep=self.sleep,
            refresh=lambda: self.calls.append(('refresh', self.now)),
            completed=complete or (lambda: self.saved), acquire=self.acquire,
            publish=publish or self.publish)

    def test_waits_for_opening_and_queries_once(self):
        self.assertEqual(self.run_worker(), 'complete_archived')
        query = [t for action, t in self.calls if action == 'query']
        self.assertEqual(query, [self.issue - dt.timedelta(minutes=30)])

    def test_one_hour_start_delay_still_collects_immediately(self):
        self.now += dt.timedelta(hours=1)
        self.assertEqual(self.run_worker(), 'complete_archived')
        self.assertIn(('query', self.issue - dt.timedelta(minutes=13)), self.calls)

    def test_reserve_refreshes_then_skips_without_query(self):
        self.now = self.issue - dt.timedelta(minutes=10)
        self.saved = True
        self.assertEqual(self.run_worker(), 'already_archived')
        self.assertFalse(any(action == 'query' for action, _ in self.calls))
        self.assertEqual(self.calls[0][0], 'refresh')

    def test_partial_attempts_repeat_at_five_minutes(self):
        self.assertEqual(self.run_worker(complete=lambda: self.saved and self.attempts == 3),
                         'complete_archived')
        times = [t for action, t in self.calls if action == 'query']
        self.assertEqual(times, [self.issue - dt.timedelta(minutes=m) for m in (30, 25, 20)])

    def test_never_queries_after_inclusive_closing(self):
        self.now = self.issue + dt.timedelta(minutes=30)
        self.assertEqual(self.run_worker(), 'complete_archived')
        self.now += dt.timedelta(microseconds=1)
        self.saved = False
        self.calls = []
        self.assertEqual(self.run_worker(), 'missed_window')
        self.assertFalse(any(action == 'query' for action, _ in self.calls))

    def test_persistence_failure_cannot_complete_or_start_another_query(self):
        def failed():
            raise RuntimeError('offline storage failure')
        with self.assertRaises(RuntimeError):
            self.run_worker(publish=failed)
        self.assertEqual(self.attempts, 1)
        self.assertFalse(self.saved)

    def test_incomplete_window_stops_after_final_attempt(self):
        self.now = self.issue + dt.timedelta(minutes=29)
        self.assertEqual(self.run_worker(complete=lambda: False), 'partial_window_closed')
        times = [t for action, t in self.calls if action == 'query']
        self.assertEqual(times, [self.issue + dt.timedelta(minutes=29),
                                 self.issue + dt.timedelta(minutes=30)])

    def test_all_schedule_pairs_keep_nominal_reference(self):
        workflow = Path(__file__).parent / '.github/workflows/meteoblue_early.yml'
        crons = re.findall(r"cron: '([^']+)'", workflow.read_text())
        self.assertEqual(set(crons), set(early.EARLY_SCHEDULE_HOURS))
        for hour in SLOT_HOURS:
            offsets = []
            for cron in crons:
                if early.EARLY_SCHEDULE_HOURS[cron] != hour:
                    continue
                minute, utc_hour, *_ = cron.split()
                offsets.append((int(utc_hour) - 3 - hour) * 60 + int(minute))
                self.assertEqual(early.nominal_issue(self.now, cron).hour, hour)
            self.assertEqual(sorted(offsets), [-73, -53])

    def test_queued_run_does_not_relabel_yesterdays_slot(self):
        actual = early.nominal_issue(self.issue + dt.timedelta(days=1), '47 10 * * *',
                                    created_at='2026-11-18T10:47:00Z')
        self.assertEqual(actual, self.issue)


class CollectorIntegrationTests(legacy.ForecastRetryTests):
    def early_env(self):
        return patch.dict(collect.os.environ, {'FORECAST_EARLY_WORKER': '1',
            'FORECAST_NOMINAL_ISSUE_AT_LOCAL': self.issue.isoformat(),
            'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_EVENT_SCHEDULE': ''})

    def test_early_complete_archive_skips_before_nominal(self):
        self.report['consulted_at'] = (self.issue - dt.timedelta(minutes=30)).isoformat()
        self.save_report('083000.json', self.report)
        legacy.FrozenDateTime.current = self.issue - dt.timedelta(minutes=20)
        with self.early_env(), patch.object(collect, 'fetch') as fetch:
            collect.main()
        fetch.assert_not_called()

    def test_early_mode_queries_only_meteoblue_and_saves_valid_frame(self):
        legacy.FrozenDateTime.current = self.issue - dt.timedelta(minutes=30)
        with self.early_env(), patch.object(collect, 'fetch', side_effect=self.response) as fetch:
            collect.main()
        self.assertEqual(fetch.call_count, len(collect.STATIONS) * 2)
        self.assertTrue(all('meteoblue' in call.args[0] for call in fetch.call_args_list))
        path = collect.meteoblue_capture_state(self.issue, self.targets)[0]
        self.assertIsNotNone(path)
        report = json.loads(path.read_text())
        self.assertEqual(report['nominal_issue_at_local'], self.issue.isoformat())
        self.assertEqual(sum(collect.values_present(row['values'])
            for site in report['station_results'].values() for row in site['forecasts']
            if row['model'].startswith('Meteoblue')), 72)

    def test_early_reference_cannot_be_used_after_its_window(self):
        legacy.FrozenDateTime.current = self.issue + dt.timedelta(days=1)
        with self.early_env(), patch.object(collect, 'fetch') as fetch:
            with self.assertRaises(SystemExit):
                collect.main()
        fetch.assert_not_called()

    def test_selection_uses_one_complete_meteoblue_frame_independent_of_openmeteo(self):
        import compare_forecasts as comparison
        original = copy.deepcopy(self.report)
        original['collection_summary'] = {'forecast_values_present': 287}
        for site in original['station_results'].values():
            for row in site['forecasts']:
                if row['model'].startswith('Meteoblue'):
                    row['values']['mean_kt'] = None
        self.save_report('090000.json', original)
        for minute, value in ((-30, 10), (-10, 20)):
            candidate = copy.deepcopy(self.report)
            acquired = self.issue + dt.timedelta(minutes=minute)
            candidate['consulted_at'] = acquired.isoformat()
            candidate['collection_summary'] = {'forecast_values_present': 72}
            for site in candidate['station_results'].values():
                site['sources'] = {package: {'retrieved_at': acquired.isoformat(),
                    'raw': {'metadata': {'utc_timeoffset': -3}}}
                    for package in ('meteoblue_basic-1h', 'meteoblue_wind-3h')}
                site['forecasts'] = [row for row in site['forecasts']
                                      if row['model'].startswith('Meteoblue')]
                for row in site['forecasts']:
                    row['values']['mean_kt'] = value
            self.save_report(acquired.strftime('%H%M%S') + '.json', candidate)
        with patch.object(comparison, 'ROOT', self.root):
            frames = comparison.forecast_archives([])
        self.assertEqual(len(frames), 1)
        sites = frames[self.issue][3]
        rows = [row for site in sites.values() for row in site['forecasts']
                if row['model'].startswith('Meteoblue')]
        self.assertEqual(len(rows), len(collect.STATIONS) * 3)
        self.assertEqual({row['values']['mean_kt'] for row in rows}, {20})
        self.assertTrue(all(row['selected_forecast_source'].endswith('085000.json') for row in rows))
        keys = {(sid, row['model'], row['target_local'], field)
                for sid, site in sites.items() for row in site['forecasts']
                if row['model'].startswith('Meteoblue') for field in collect.VARS}
        self.assertEqual(len(keys), 72)
