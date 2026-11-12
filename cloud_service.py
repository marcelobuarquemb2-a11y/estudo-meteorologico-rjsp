"""Authenticated Cloud Run receiver. Cloud Run IAM authenticates Scheduler's OIDC token."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from flask import Flask, request
from study_config import SLOT_HOURS, TZ, local_time

app = Flask(__name__)
CODE_ROOT = Path(__file__).resolve().parent


def nominal_time(scheduled_at, hour, day_offset=0):
    when = local_time(scheduled_at)
    if hour not in SLOT_HOURS or day_offset not in (-1, 0):
        raise ValueError('Invalid reference hour or day offset')
    return (when + dt.timedelta(days=day_offset)).replace(hour=hour, minute=0, second=0, microsecond=0)


def bucket():
    from google.cloud import storage
    return storage.Client().bucket(os.environ['ARTIFACT_BUCKET'])


def prefix():
    return os.environ.get('ARTIFACT_PREFIX', 'parallel-v1').strip('/') + '/'


def create_json(blob, obj):
    blob.upload_from_string(json.dumps(obj, ensure_ascii=False, allow_nan=False),
                            content_type='application/json', if_generation_match=0, timeout=60)


def claim(blob, now):
    """A generation-checked lease survives process/instance failure. Runtime is limited to 840s."""
    from google.api_core.exceptions import PreconditionFailed, NotFound
    try:
        create_json(blob, {'expires_at': (now + dt.timedelta(minutes=35)).isoformat()})
        return blob.generation
    except PreconditionFailed:
        try:
            blob.reload(timeout=60)
            generation = blob.generation
            lease = json.loads(blob.download_as_bytes(if_generation_match=generation, timeout=60))
            if local_time(lease['expires_at']) > now:
                return None
            blob.delete(if_generation_match=generation, timeout=60)
        except (PreconditionFailed, NotFound):
            return None
        try:
            create_json(blob, {'expires_at': (now + dt.timedelta(minutes=35)).isoformat()})
            return blob.generation
        except PreconditionFailed:
            return None


def hydrate(store, root, kind, nominal):
    days = [nominal.date(), nominal.date() + dt.timedelta(days=1)]
    folders = ([f'data/{nominal:%Y-%m}/{nominal:%Y-%m-%d}/'] if kind == 'forecast'
               else [f'data/observations/reports/{day}/' for day in days])
    for folder in folders:
        for blob in store.list_blobs(prefix=prefix() + folder):
            relative = blob.name[len(prefix()):]
            if not relative.endswith('.json'):
                continue
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob.download_as_bytes(timeout=60))


def coverage(root, kind, nominal):
    if kind == 'forecast':
        # Import after environment initialization happens in subprocess; here use data only.
        files = (root / f'data/{nominal:%Y-%m}/{nominal:%Y-%m-%d}').glob('*.json')
        reports = [json.loads(p.read_text()) for p in files]
        reports = [r for r in reports if r.get('nominal_issue_at_local') == nominal.isoformat() and r.get('official_series_eligible')]
        return max((r.get('collection_summary', {}).get('forecast_values_present', 0) for r in reports), default=0), 288
    reports = [json.loads(p.read_text()) for p in (root / 'data/observations/reports').glob('*/*.json')]
    reports = [r for r in reports if r.get('target_local') == nominal.isoformat()]
    # Preserve coverage even for hydrated legacy reports.
    def count(r):
        return sum(sum(type(v) in (int, float) and __import__('math').isfinite(v) for v in (s.get('observed') or {}).values()) for s in r.get('stations', {}).values())
    return max(map(count, reports), default=0), 24


@app.get('/healthz')
def health():
    return {'status': 'ready'}


@app.post('/collect/<kind>')
def collect(kind):
    if kind not in ('forecast', 'observations'):
        return {'error': 'unknown collector'}, 404
    scheduled = request.headers.get('X-CloudScheduler-ScheduleTime')
    job = request.headers.get('X-CloudScheduler-JobName')
    body = request.get_json(silent=True) or {}
    if not scheduled or not job:
        return {'error': 'Missing Scheduler identity/scheduled timestamp'}, 400
    try:
        nominal = nominal_time(scheduled, body['hour'], body.get('day_offset', 0))
    except (KeyError, ValueError, TypeError):
        return {'error': 'Invalid nominal time'}, 400
    now = dt.datetime.now(TZ)
    store = bucket()
    event_id = hashlib.sha256((job + '|' + scheduled + '|' + kind).encode()).hexdigest()
    done = store.blob(prefix() + 'completed/' + event_id + '.json')
    if done.exists(timeout=60):
        return {'status': 'duplicate', 'event_id': event_id}
    lock = store.blob(prefix() + f'leases/{kind}/{nominal:%Y%m%d_%H}.json')
    generation = claim(lock, now)
    if generation is None:
        return {'status': 'busy'}, 503
    try:
        # Recheck after the lease; another request may have completed while we waited.
        if done.exists(timeout=60):
            return {'status': 'duplicate', 'event_id': event_id}
        audit = {'event_id': event_id, 'kind': kind, 'scheduled_at': scheduled,
                 'nominal_issue_at_local': nominal.isoformat(), 'started_at': now.isoformat(),
                 'origin': 'cloud-run-parallel-v1'}
        if kind == 'forecast' and abs(now - nominal) > dt.timedelta(minutes=30):
            audit.update(status='deadline_missed', present=0, expected=288)
            code = 200
        else:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                hydrate(store, root, kind, nominal)
                env = {**os.environ, 'RJSP_DATA_ROOT': temp, 'GITHUB_EVENT_NAME': 'cloud_scheduler',
                       'GITHUB_EVENT_SCHEDULE': '', 'NOMINAL_ISSUE_BRT': nominal.isoformat(),
                       'OBS_TARGET_BRT': nominal.isoformat()}
                script = 'collect.py' if kind == 'forecast' else 'collect_observations.py'
                try:
                    result = subprocess.run([sys.executable, str(CODE_ROOT / script)], env=env,
                                            cwd=CODE_ROOT, capture_output=True, text=True, timeout=840)
                    present, expected = coverage(root, kind, nominal)
                    audit.update(status='complete' if result.returncode == 0 and present == expected else 'incomplete',
                                 present=present, expected=expected, exit_code=result.returncode)
                    # Do not store stderr: exceptions from provider libraries can contain secret URLs.
                    print(json.dumps({**audit, 'collector_output': result.stdout[-4000:]}), flush=True)
                    code = 200 if audit['status'] == 'complete' else 503
                except subprocess.TimeoutExpired:
                    audit.update(status='collector_timeout')
                    code = 503
        audit['finished_at'] = dt.datetime.now(TZ).isoformat()
        create_json(store.blob(prefix() + 'execution_audits/' + event_id + '-' + uuid.uuid4().hex + '.json'), audit)
        print(json.dumps(audit), flush=True)
        if code == 200:
            create_json(done, audit)
        return audit, code
    finally:
        lock.delete(if_generation_match=generation, timeout=60)
