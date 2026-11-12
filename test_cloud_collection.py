"""Offline failure-path tests: real historical replay, deadlines, durable storage and delivery."""
import datetime as dt
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from google.api_core.exceptions import PreconditionFailed
import artifact_store
import cloud_service as cloud
import collect_observations as obs
from study_config import TZ


class FakeBlob:
    def __init__(self, store, name):
        self.store, self.name, self.generation = store, name, None
    def exists(self, **kwargs):
        return self.name in self.store.records
    def upload_from_string(self, data, if_generation_match=None, **kwargs):
        if if_generation_match == 0 and self.exists():
            raise PreconditionFailed('exists')
        self.store.counter += 1
        self.generation = self.store.counter
        self.store.records[self.name] = (data.encode() if isinstance(data, str) else data, self.generation)
    def reload(self, **kwargs):
        self.generation = self.store.records[self.name][1]
    def download_as_bytes(self, if_generation_match=None, **kwargs):
        if if_generation_match is not None and self.store.records[self.name][1] != if_generation_match:
            raise PreconditionFailed('generation changed')
        return self.store.records[self.name][0]
    def delete(self, if_generation_match=None, **kwargs):
        if self.store.records[self.name][1] != if_generation_match:
            raise PreconditionFailed('generation changed')
        del self.store.records[self.name]


class FakeBucket:
    def __init__(self):
        self.records, self.counter = {}, 0
    def blob(self, name):
        return FakeBlob(self, name)
    def list_blobs(self, prefix):
        return [self.blob(name) for name in list(self.records) if name.startswith(prefix)]


class FrozenDateTime(dt.datetime):
    current = dt.datetime(2026, 11, 12, 12, tzinfo=TZ)
    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeBucket()
        self.enterContext(patch.object(cloud, 'bucket', return_value=self.store))
        self.enterContext(patch.dict(os.environ, {'ARTIFACT_PREFIX': 'parallel-v1'}))
        self.enterContext(patch.object(cloud.dt, 'datetime', FrozenDateTime))
        FrozenDateTime.current = dt.datetime(2026, 11, 12, 12, tzinfo=TZ)
        self.client = cloud.app.test_client()
        self.headers = {'X-CloudScheduler-ScheduleTime': '2026-11-12T15:00:00Z',
                        'X-CloudScheduler-JobName': 'projects/p/locations/r/jobs/noon'}

    def post(self, kind='forecast', hour=12):
        return self.client.post('/collect/' + kind, json={'hour': hour}, headers=self.headers)

    def test_scheduled_date_survives_midnight_and_yesterday_recovery(self):
        self.assertEqual(cloud.nominal_time('2026-11-12T21:00:00Z', 18).isoformat(), '2026-11-12T18:00:00-03:00')
        self.assertEqual(cloud.nominal_time('2026-11-13T11:40:00Z', 18, -1).isoformat(), '2026-11-12T18:00:00-03:00')

    def test_late_forecast_is_audited_without_provider_calls_and_duplicate_is_noop(self):
        FrozenDateTime.current += dt.timedelta(minutes=31)
        with patch.object(cloud.subprocess, 'run') as run:
            response = self.post()
            self.assertEqual(response.json['status'], 'deadline_missed')
            self.assertEqual(self.post().json['status'], 'duplicate')
            run.assert_not_called()
        self.assertTrue(any('execution_audits/' in key for key in self.store.records))

    def test_missing_scheduler_headers_rejected(self):
        self.assertEqual(self.client.post('/collect/forecast', json={'hour': 12}).status_code, 400)

    def test_partial_and_storage_failure_never_create_success_marker(self):
        with patch.object(cloud, 'hydrate'), patch.object(cloud, 'coverage', return_value=(287, 288)), patch.object(cloud.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='partial')):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(any('/completed/' in key for key in self.store.records))
        self.assertFalse(any('/leases/' in key for key in self.store.records))

    def test_complete_delivery_is_idempotent(self):
        with patch.object(cloud, 'hydrate'), patch.object(cloud, 'coverage', return_value=(288, 288)), patch.object(cloud.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='complete')) as run:
            self.assertEqual(self.post().json['status'], 'complete')
            self.assertEqual(self.post().json['status'], 'duplicate')
            self.assertEqual(run.call_count, 1)

    def test_late_actuals_keep_original_target(self):
        FrozenDateTime.current += dt.timedelta(hours=3)
        with patch.object(cloud, 'hydrate'), patch.object(cloud, 'coverage', return_value=(24, 24)), patch.object(cloud.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='')) as run:
            self.assertEqual(self.post('observations').status_code, 200)
            self.assertEqual(run.call_args.kwargs['env']['OBS_TARGET_BRT'], '2026-11-12T12:00:00-03:00')

    def test_active_lease_blocks_overlapping_attempt(self):
        lease = self.store.blob('parallel-v1/leases/forecast/20261112_12.json')
        cloud.claim(lease, FrozenDateTime.current)
        with patch.object(cloud.subprocess, 'run') as run:
            self.assertEqual(self.post().status_code, 503)
            run.assert_not_called()

    def test_expired_lease_can_be_reclaimed(self):
        lease = self.store.blob('parallel-v1/leases/forecast/20261112_12.json')
        first = cloud.claim(lease, FrozenDateTime.current - dt.timedelta(hours=1))
        second = cloud.claim(lease, FrozenDateTime.current)
        self.assertIsNotNone(second)
        self.assertNotEqual(first, second)

    def test_timeout_remains_retryable_and_releases_lease(self):
        with patch.object(cloud, 'hydrate'), patch.object(cloud.subprocess, 'run', side_effect=cloud.subprocess.TimeoutExpired('collector', 840)):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json['status'], 'collector_timeout')
        self.assertFalse(any('/completed/' in key or '/leases/' in key for key in self.store.records))

    def test_hydrates_original_day_after_midnight(self):
        nominal = dt.datetime(2026, 11, 12, 18, tzinfo=TZ)
        cloud.create_json(self.store.blob('parallel-v1/data/observations/reports/2026-11-12/a.json'), {'day': 12})
        cloud.create_json(self.store.blob('parallel-v1/data/observations/reports/2026-11-13/b.json'), {'day': 13})
        with tempfile.TemporaryDirectory() as temp:
            cloud.hydrate(self.store, Path(temp), 'observations', nominal)
            self.assertEqual(len(list(Path(temp).rglob('*.json'))), 2)


class SourceDeadlineTests(unittest.TestCase):
    def test_each_source_query_checks_actual_start_before_network(self):
        import collect
        nominal = dt.datetime(2026, 11, 12, 12, tzinfo=TZ)
        FrozenDateTime.current = nominal + dt.timedelta(minutes=30, microseconds=1)
        with patch.object(collect.dt, 'datetime', FrozenDateTime), patch.object(collect, 'fetch') as fetch:
            with self.assertRaises(TimeoutError):
                collect.query_source('https://example.invalid', {}, nominal, True)
            fetch.assert_not_called()



class PersistenceTests(unittest.TestCase):
    def test_failure_to_upload_is_not_reported_as_success_and_local_evidence_remains(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'RJSP_DATA_ROOT': temp, 'ARTIFACT_BUCKET': 'test'}):
            path = Path(temp) / 'data/raw.json'
            with patch('google.cloud.storage.Client') as client:
                client.return_value.bucket.return_value.blob.return_value.upload_from_string.side_effect = RuntimeError('offline')
                with self.assertRaises(artifact_store.StorageError):
                    artifact_store.save_text(path, '{"rain":0}')
            self.assertEqual(json.loads(path.read_text()), {'rain': 0})

    def test_immutable_collision_is_never_overwritten(self):
        store = FakeBucket()
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'RJSP_DATA_ROOT': temp, 'ARTIFACT_BUCKET': 'test', 'ARTIFACT_PREFIX': 'parallel-v1'}), patch('google.cloud.storage.Client') as client:
            client.return_value.bucket.return_value = store
            path = Path(temp) / 'data/raw.json'
            artifact_store.save_text(path, '{"rain":0}')
            artifact_store.save_text(path, '{"rain":0}')
            with self.assertRaises(artifact_store.StorageError):
                artifact_store.save_text(path, '{"rain":1}')
            self.assertEqual(store.blob('parallel-v1/data/raw.json').download_as_bytes(), b'{"rain":0}')


class HistoricalReplayTests(unittest.TestCase):
    def test_ilha_fiscal_15h_direction_exists_but_hourly_sequence_has_gaps(self):
        path = Path(__file__).parent / 'data/observations/raw/2026-11-11/184911875698_20261111_1500/portosrio-ilha_fiscal-2026-11-11.json'
        payload = json.loads(path.read_text())
        indexed = obs.sma_index(payload, obs.PORTOSRIO['ilha_fiscal'])
        target = dt.datetime(2026, 11, 11, 15, tzinfo=TZ)
        rows = [obs.sma_record(indexed, target + dt.timedelta(minutes=m)) for m in (0, 5, 10, 15, 20, 25)]
        selected = obs.best_observation(rows, target, target + dt.timedelta(minutes=30))
        self.assertIsNone(selected['observed']['direction_deg'])
        self.assertEqual(selected['indicator_candidates']['direction_deg'], {'value': 159, 'observed_at_local': target.isoformat()})
        exact = rows[0]
        self.assertEqual(exact['missing_five_minute_slots']['gust_kt'], ['2026-11-11T14:10:00-03:00', '2026-11-11T14:15:00-03:00'])
        self.assertIsNone(exact['observed']['precip_mm'])


if __name__ == '__main__':
    unittest.main()
