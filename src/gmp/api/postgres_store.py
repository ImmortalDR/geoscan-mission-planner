"""PostgreSQL JSONB workspace; all mutations are committed transactions."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from .workspace_store import WorkspaceStore, expiry, utcnow


class PostgresWorkspaceStore(WorkspaceStore):
    backend = "postgresql"

    def __init__(self, root: Path, dsn: str):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.dsn = dsn
        with self.connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(763501)")
            db.execute("CREATE TABLE IF NOT EXISTS configuration (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS scenes (id TEXT PRIMARY KEY, record JSONB NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, record JSONB NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS plans_scene ON plans(scene_id)")
            db.execute("CREATE INDEX IF NOT EXISTS plans_status ON plans ((record->>'status'))")
            db.execute("CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL, username TEXT NOT NULL, role TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS login_attempts (ip TEXT NOT NULL, at DOUBLE PRECISION NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS login_ip ON login_attempts(ip,at)")
            db.execute("INSERT INTO configuration VALUES ('schema_version','1') ON CONFLICT (key) DO NOTHING")
            if db.execute("SELECT value FROM configuration WHERE key='schema_version'").fetchone()[0] != "1":
                raise RuntimeError("Unsupported PostgreSQL workspace schema version")

    def connect(self):
        for attempt in range(2):
            try:
                return psycopg.connect(self.dsn, connect_timeout=10, application_name="geoscan",
                                       options="-c statement_timeout=15000 -c lock_timeout=5000")
            except psycopg.OperationalError:
                if attempt:
                    raise
                # Retry connection setup only. Never replay a possibly committed mutation.
                time.sleep(.25)

    def bind_access_code(self, code):
        with self.connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(763502)")
            db.execute("INSERT INTO configuration VALUES ('session_salt',%s) ON CONFLICT DO NOTHING", (secrets.token_hex(32),))
            salt = db.execute("SELECT value FROM configuration WHERE key='session_salt'").fetchone()[0]
            digest = hmac.new(salt.encode(), code.encode(), hashlib.sha256).hexdigest()
            row = db.execute("SELECT value FROM configuration WHERE key='access_version'").fetchone()
            if not row or row[0] != digest:
                db.execute("DELETE FROM sessions")
                db.execute("INSERT INTO configuration VALUES ('access_version',%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (digest,))

    def put_scene(self, record):
        with self.connect() as db:
            db.execute("INSERT INTO scenes VALUES (%s,%s) ON CONFLICT (id) DO UPDATE SET record=EXCLUDED.record", (record["id"], Jsonb(record)))

    def get_scene(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT record FROM scenes WHERE id=%s", (identifier,)).fetchone()
        return row[0] if row else None

    def scenes(self):
        with self.connect() as db:
            return [r[0] for r in db.execute("SELECT record FROM scenes ORDER BY record->>'created_at' DESC")]

    def save_scenario(self, identifier):
        with self.connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(763503)")
            row = db.execute("SELECT record FROM scenes WHERE id=%s FOR UPDATE", (identifier,)).fetchone()
            if not row:
                raise ValueError("Scene not found")
            record = row[0]
            library = record.get("library_id", identifier)
            db.execute("UPDATE scenes SET record=record || %s WHERE id<>%s AND record->>'saved'='true' AND COALESCE(record->>'library_id',id)=%s",
                       (Jsonb({"saved": False, "expires_at": expiry()}), identifier, library))
            record.update(saved=True, library_id=library, saved_at=utcnow(), expires_at=None)
            db.execute("UPDATE scenes SET record=%s WHERE id=%s", (Jsonb(record), identifier))
        return record

    def rename_scenario(self, identifier, name):
        with self.connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(763503)")
            scenes = [r[0] for r in db.execute("SELECT record FROM scenes FOR UPDATE")]
            record = next((r for r in scenes if r["id"] == identifier), None)
            if record is None:
                raise ValueError("Scene not found")
            if record.get("template_readonly"):
                raise ValueError("Template is read-only; use Save As")
            library = record.get("library_id", identifier)
            if any(r.get("saved") and r.get("library_id", r["id"]) != library and r["name"].casefold() == name.casefold() for r in scenes):
                raise ValueError("A saved scenario with this name already exists")
            for r in scenes:
                if r.get("library_id", r["id"]) == library:
                    r["name"] = name
                    db.execute("UPDATE scenes SET record=%s WHERE id=%s", (Jsonb(r), r["id"]))
        return record

    def put_plan(self, record):
        record["updated_at"] = utcnow()
        with self.connect() as db:
            db.execute("INSERT INTO plans VALUES (%s,%s,%s) ON CONFLICT (id) DO UPDATE SET scene_id=EXCLUDED.scene_id, record=EXCLUDED.record", (record["id"], record["scene_id"], Jsonb(record)))

    def get_plan(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT record FROM plans WHERE id=%s", (identifier,)).fetchone()
        return row[0] if row else None

    def plan_status(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT record->>'status' FROM plans WHERE id=%s", (identifier,)).fetchone()
        return row[0] if row else None

    def plans(self, scene_id=None):
        with self.connect() as db:
            return [r[0] for r in db.execute("SELECT record FROM plans WHERE (%s::text IS NULL OR scene_id=%s) ORDER BY record->>'created_at' DESC", (scene_id, scene_id))]

    def plan_headers(self, scene_id=None, limit=None, offset=0):
        # JSONB subtraction keeps large trajectories out of polling responses.
        with self.connect() as db:
            rows = db.execute("SELECT record - ARRAY['plan','validation','certificate','recommendations','comparison','progress']::text[] FROM plans WHERE (%s::text IS NULL OR scene_id=%s) ORDER BY record->>'created_at' DESC LIMIT %s OFFSET %s", (scene_id, scene_id, limit, offset))
            return [r[0] for r in rows]

    def latest_h1_run(self, scene):
        if not scene.get("saved"):
            return None
        with self.connect() as db:
            row = db.execute("SELECT p.record - ARRAY['plan','validation','certificate','progress']::text[] FROM plans p LEFT JOIN scenes s ON p.scene_id=s.id WHERE COALESCE(p.record->>'library_id',s.record->>'library_id',s.id)=%s AND p.record->>'mode'='h1' ORDER BY p.record->>'created_at' DESC LIMIT 1", (scene.get("library_id", scene["id"]),)).fetchone()
        if row:
            row[0]["matches_input"] = row[0]["input_sha256"] == scene["input_sha256"]
            return row[0]
        return None

    def active_scene_ids(self):
        with self.connect() as db:
            return [r[0] for r in db.execute("SELECT scene_id FROM plans WHERE record->>'status' IN ('queued','running')")]

    def expire(self):
        with self.connect() as db:
            rows = db.execute("SELECT id,record FROM scenes WHERE record->>'expires_at'<%s AND id NOT IN (SELECT scene_id FROM plans WHERE record->>'status' IN ('queued','running')) FOR UPDATE", (utcnow(),)).fetchall()
            for identifier, _ in rows:
                db.execute("DELETE FROM plans WHERE scene_id=%s", (identifier,))
                db.execute("DELETE FROM scenes WHERE id=%s", (identifier,))
            db.execute("DELETE FROM sessions WHERE expires<%s", (time.time(),))
            db.execute("DELETE FROM login_attempts WHERE at<%s", (time.time()-900,))
        return [r[1] for r in rows]

    def login_allowed(self, ip):
        with self.connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(763504)")
            now = time.time()
            db.execute("DELETE FROM login_attempts WHERE at<%s", (now-900,))
            count = db.execute("SELECT COUNT(*) FROM login_attempts WHERE ip=%s", (ip,)).fetchone()[0]
            total = db.execute("SELECT COUNT(*) FROM login_attempts WHERE at>%s", (now-60,)).fetchone()[0]
            if count >= 5 or total >= 100:
                return False
            db.execute("INSERT INTO login_attempts VALUES (%s,%s)", (ip, now))
        return True

    def login_success(self, ip):
        with self.connect() as db:
            db.execute("DELETE FROM login_attempts WHERE ip=%s", (ip,))

    def create_session(self, username="legacy", role="admin"):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES (%s,%s,%s,%s,%s)", (hashlib.sha256(token.encode()).hexdigest(), csrf, time.time()+43200, username, role))
        return token, csrf

    def principal(self, token):
        if not token or len(token) > 128:
            return None
        with self.connect() as db:
            row = db.execute("SELECT csrf,username,role FROM sessions WHERE token=%s AND expires>%s", (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return dict(zip(("csrf_token", "username", "role"), row)) if row else None

    def session(self, token):
        user = self.principal(token)
        return user["csrf_token"] if user else None

    def logout(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token=%s", (hashlib.sha256(token.encode()).hexdigest(),))
