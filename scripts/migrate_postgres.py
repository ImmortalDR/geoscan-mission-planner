#!/usr/bin/env python3
"""Copy an offline SQLite workspace into an empty PostgreSQL database."""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gmp.api.postgres_store import PostgresWorkspaceStore
from psycopg.types.json import Jsonb


def migrate(source: Path, destination: Path, dsn: str, execute=False):
    source, destination = source.resolve(), destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Use separate, non-nested source and destination directories")
    with closing(sqlite3.connect(f"file:{source / 'workspace.sqlite3'}?mode=ro", uri=True)) as db:
        scenes = [json.loads(row[0]) for row in db.execute("SELECT record FROM scenes")]
        plans = [json.loads(row[0]) for row in db.execute("SELECT record FROM plans")]
    if any(p["status"] in {"queued", "running"} for p in plans):
        raise ValueError("Stop admission and finish or cancel active plans before migration")
    report = {"scenes": len(scenes), "plans": len(plans), "executed": False, "sessions_migrated": 0}
    if not execute:
        return report
    store = PostgresWorkspaceStore(destination, dsn)
    with store.connect() as db:
        db.execute("SELECT pg_advisory_xact_lock(763505)")
        if db.execute("SELECT (SELECT COUNT(*) FROM scenes)+(SELECT COUNT(*) FROM plans)").fetchone()[0]:
            raise ValueError("Destination database must be empty")
        manifest = {}
        for folder in ("scenes", "jobs"):
            if not (source / folder).exists():
                continue
            if (destination / folder).exists():
                raise ValueError("Destination files already exist; use a fresh destination")
            for path in (source / folder).rglob("*"):
                if path.is_symlink():
                    raise ValueError("Workspace must not contain symlinks")
                if path.is_file():
                    manifest[str(path.relative_to(source))] = hashlib.sha256(path.read_bytes()).hexdigest()
            shutil.copytree(source / folder, destination / folder)
        for rel, digest in manifest.items():
            if any(hashlib.sha256((root / rel).read_bytes()).hexdigest() != digest for root in (source, destination)):
                raise ValueError("Source changed or copy failed; migration aborted")
        for record in scenes:
            db.execute("INSERT INTO scenes VALUES (%s,%s)", (record["id"], Jsonb(record)))
        for record in plans:
            # Historical logs remain in the source backup; results and certificates are retained.
            record.pop("log_dir", None)
            db.execute("INSERT INTO plans VALUES (%s,%s,%s)", (record["id"], record["scene_id"], Jsonb(record)))
    report.update(executed=True, files_verified=len(manifest))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.destination, os.environ.get("GMP_DATABASE_URL", ""), args.execute), indent=2))
