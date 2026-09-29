"""Large flight geometry must not be read to list scenes or poll statuses."""
from contextlib import contextmanager
import json
import sqlite3

from gmp.api.workspace_store import WorkspaceStore


def example():
    return dict(id='p',scene_id='s',library_id='library',mode='h1',status='running',
                created_at='2026-09-27T00:00:00+00:00',input_sha256='input',
                progress=[dict(stage='h2',h2_output={'large_geometry':'x'*100000})],
                plan={'preserved':'x'*100000})


def test_metadata_operations_never_read_full_plan(tmp_path,monkeypatch):
    store=WorkspaceStore(tmp_path)
    scene=dict(id='s',library_id='library',saved=True,input_sha256='input',created_at='2026-09-27')
    store.put_scene(scene);record=example();store.put_plan(record)
    original=store.connect
    @contextmanager
    def metadata_only():
        with original() as db:
            db.set_authorizer(lambda action,table,column,*rest:
                sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_READ and table=='plans' and column=='record'
                else sqlite3.SQLITE_OK)
            yield db
    monkeypatch.setattr(store,'connect',metadata_only)
    headers=store.plan_headers('s',limit=1)
    assert headers[0]['id']=='p' and len(json.dumps(headers))<2000
    assert 'progress' not in headers[0] and 'plan' not in headers[0]
    assert store.plan_status('p')=='running'
    assert store.active_scene_ids()==['s']
    assert store.latest_h1_run(scene)['matches_input']


def test_legacy_backfill_preserves_result_and_tracks_updates_and_deletes(tmp_path):
    store=WorkspaceStore(tmp_path);record=example()
    # Simulate a database created before the compact metadata table existed.
    with store.connect() as db:
        db.execute('INSERT INTO plans VALUES (?,?,?)',('p','s',json.dumps(record)))
    restored=WorkspaceStore(tmp_path)
    assert restored.get_plan('p')==record
    assert restored.plan_status('p')=='running'
    record['status']='COMPLETED';restored.put_plan(record)
    assert restored.plan_status('p')=='COMPLETED' and restored.active_scene_ids()==[]
    assert restored.get_plan('p')['progress']==record['progress']
    with restored.connect() as db:db.execute('DELETE FROM plans WHERE id=?',('p',))
    assert restored.plan_headers()==[] and restored.plan_status('p') is None
