"""Write local evidence atomically; acknowledge only after primary cloud persistence."""
import os
from pathlib import Path
import tempfile


class StorageError(RuntimeError):
    pass


def save_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as out:
        out.write(text)
        staged = Path(out.name)
    staged.replace(path)
    bucket = os.environ.get('ARTIFACT_BUCKET')
    if bucket:
        from google.cloud import storage
        from google.api_core.exceptions import PreconditionFailed
        root = Path(os.environ['RJSP_DATA_ROOT']).resolve()
        key = os.environ.get('ARTIFACT_PREFIX', 'parallel-v1').strip('/') + '/' + path.resolve().relative_to(root).as_posix()
        blob = storage.Client().bucket(bucket).blob(key)
        try:
            blob.upload_from_string(text, content_type='application/json', if_generation_match=0, timeout=60)
        except PreconditionFailed:
            if blob.download_as_bytes(timeout=60) != text.encode('utf-8'):
                raise StorageError('Immutable artifact collision: ' + key)
        except Exception as exc:
            raise StorageError('Primary artifact persistence failed: ' + key) from exc
