"""Inspectable, temporary H1 run artifacts; never change the H1/H2 bundle."""
from __future__ import annotations

import json
from pathlib import Path


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def update_run_log(record: dict, write=atomic_json) -> None:
    if not record.get('log_dir'):
        return
    directory = Path(record['log_dir'])
    directory.mkdir(parents=True, exist_ok=True)
    bundle = (record.get('plan') or {}).get('h1_bundle')
    if bundle is not None:
        write(directory / 'h1_h2.bundle.json', bundle)
    events = [{key: value for key, value in event.items() if key not in ('h1_output', 'h2_output')}
              for event in record.get('progress', [])]
    summary = {key: record.get(key) for key in (
        'run_code', 'id', 'scene_id', 'library_id', 'scenario_name', 'input_sha256',
        'created_at', 'updated_at', 'mode', 'status', 'error')}
    summary.update(progress=events, bundle_file='h1_h2.bundle.json' if bundle is not None else None)
    if bundle is not None:
        summary['task_count'] = len(bundle.get('tasks', []))
        summary['transect_count'] = sum(len(task.get('transects', [])) for task in bundle.get('tasks', []))
    write(directory / 'run.json', summary)
