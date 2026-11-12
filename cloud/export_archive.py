#!/usr/bin/env python3
"""Copy immutable parallel-run evidence for GitHub backup; never overwrite official data."""
import argparse
from pathlib import Path
from google.cloud import storage


def export(bucket_name, destination, prefix='parallel-v1'):
    root = Path(destination).resolve()
    count = 0
    for blob in storage.Client().list_blobs(bucket_name, prefix=prefix.strip('/') + '/'):
        relative = blob.name[len(prefix.strip('/')) + 1:]
        if not relative.endswith('.json') or not relative.startswith(('data/', 'execution_audits/')):
            continue
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Unsafe object path')
        content = blob.download_as_bytes(timeout=60)
        if path.exists():
            if path.read_bytes() != content:
                raise ValueError('Immutable backup collision: ' + relative)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        count += 1
    print('New immutable artifacts copied:', count)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--destination', default='data/cloud_parallel/parallel-v1')
    args = parser.parse_args()
    export(args.bucket, args.destination)
