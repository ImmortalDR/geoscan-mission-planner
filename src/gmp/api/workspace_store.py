"""Durable, isolated H3 workspace. Database errors never fall back to memory."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLAN_HEADER_FIELDS = ("status", "objective", "mode", "created_at", "updated_at", "metrics",
                      "input_sha256", "result_status", "run_code", "library_id", "log_dir")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkspaceStore:
    backend = "sqlite"
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "workspace.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS scenes(id TEXT PRIMARY KEY, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, record TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS plans_scene ON plans(scene_id);
                CREATE TABLE IF NOT EXISTS plan_summaries(id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, record TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS plan_summaries_scene ON plan_summaries(scene_id);
                CREATE TRIGGER IF NOT EXISTS delete_plan_summary AFTER DELETE ON plans
                    BEGIN DELETE FROM plan_summaries WHERE id=OLD.id; END;
                CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS login_attempts(ip TEXT NOT NULL, at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS login_ip ON login_attempts(ip,at);
                CREATE TABLE IF NOT EXISTS configuration(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
            for name, default in (("username", "legacy"), ("role", "admin")):
                if name not in columns:
                    db.execute(f"ALTER TABLE sessions ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")
            # Backfill compact metadata once. Listing scenes used to parse every
            # large trajectory repeatedly for each saved library, causing OOM.
            pairs=",".join(f"'{key}',json_extract(record,'$.{key}')" for key in PLAN_HEADER_FIELDS)
            missing=db.execute("SELECT id FROM plans WHERE id NOT IN (SELECT id FROM plan_summaries)").fetchall()
            for (identifier,) in missing:
                db.execute(f"INSERT INTO plan_summaries SELECT id,scene_id,json_object('id',id,'scene_id',scene_id,{pairs}) FROM plans WHERE id=?",(identifier,))
        self.path.chmod(0o600)

    def bind_access_code(self, code: str) -> None:
        """Rotating the environment code invalidates existing shared sessions."""
        import hmac
        with self.connect() as db:
            row = db.execute("SELECT value FROM configuration WHERE key='session_salt'").fetchone()
            salt = row[0] if row else secrets.token_hex(32)
            db.execute("INSERT OR IGNORE INTO configuration VALUES ('session_salt',?)", (salt,))
            digest = hmac.new(salt.encode(), code.encode(), hashlib.sha256).hexdigest()
            previous = db.execute("SELECT value FROM configuration WHERE key='access_version'").fetchone()
            if previous is None or previous[0] != digest:
                db.execute("DELETE FROM sessions")
                db.execute("INSERT OR REPLACE INTO configuration VALUES ('access_version',?)", (digest,))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("PRAGMA busy_timeout=15000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def health(self):
        with self.connect() as db:
            db.execute("SELECT 1").fetchone()

    def put_scene(self, record: dict) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO scenes VALUES (?,?)", (record["id"], json.dumps(record, allow_nan=False)))

    def save_scenario(self, identifier: str) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record FROM scenes WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise ValueError("Scene not found")
            record = json.loads(row[0])
            library_id = record.get("library_id", identifier)
            for old_id, raw in db.execute("SELECT id,record FROM scenes").fetchall():
                old = json.loads(raw)
                if old_id != identifier and old.get("saved") and old.get("library_id", old_id) == library_id:
                    old.update(saved=False, expires_at=expiry())
                    db.execute("UPDATE scenes SET record=? WHERE id=?", (json.dumps(old), old_id))
            record.update(saved=True, library_id=library_id, saved_at=utcnow(), expires_at=None)
            db.execute("UPDATE scenes SET record=? WHERE id=?", (json.dumps(record), identifier))
        return record

    def rename_scenario(self, identifier: str, name: str) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            scenes = [json.loads(row[0]) for row in db.execute("SELECT record FROM scenes").fetchall()]
            record = next((item for item in scenes if item["id"] == identifier), None)
            if record is None:
                raise ValueError("Scene not found")
            if record.get("template_readonly"):
                raise ValueError("Template is read-only; use Save As")
            library_id = record.get("library_id", identifier)
            if any(item.get("saved") and item.get("library_id", item["id"]) != library_id
                   and item["name"].casefold() == name.casefold() for item in scenes):
                raise ValueError("A saved scenario with this name already exists")
            for item in scenes:
                if item.get("library_id", item["id"]) == library_id:
                    item["name"] = name
                    db.execute("UPDATE scenes SET record=? WHERE id=?", (json.dumps(item, allow_nan=False), item["id"]))
        return record

    def get_scene(self, identifier: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT record FROM scenes WHERE id=?", (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def scenes(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT record FROM scenes").fetchall()
        return sorted((json.loads(r[0]) for r in rows), key=lambda r: r["created_at"], reverse=True)

    def put_plan(self, record: dict) -> None:
        record["updated_at"] = utcnow()
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO plans VALUES (?,?,?)", (record["id"], record["scene_id"], json.dumps(record, allow_nan=False)))
            header={key:record.get(key) for key in PLAN_HEADER_FIELDS}
            header.update(id=record['id'],scene_id=record['scene_id'])
            db.execute("INSERT OR REPLACE INTO plan_summaries VALUES (?,?,?)",(record['id'],record['scene_id'],json.dumps(header,allow_nan=False)))

    def get_plan(self, identifier: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT record FROM plans WHERE id=?", (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def plan_status(self, identifier: str) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT json_extract(record,'$.status') FROM plan_summaries WHERE id=?", (identifier,)).fetchone()
        return row[0] if row else None

    def plans(self, scene_id: str | None = None) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT record FROM plans WHERE scene_id=?", (scene_id,)).fetchall() if scene_id else db.execute("SELECT record FROM plans").fetchall()
        return sorted((json.loads(r[0]) for r in rows), key=lambda r: r["created_at"], reverse=True)

    def plan_headers(self, scene_id: str | None = None, limit: int | None = None, offset: int = 0) -> list[dict]:
        sql = "SELECT record FROM plan_summaries"
        params = []
        if scene_id is not None:
            sql += " WHERE scene_id=?"
            params.append(scene_id)
        sql += " ORDER BY json_extract(record,'$.created_at') DESC"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend((limit, offset))
        with self.connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [json.loads(row[0]) for row in rows]

    def latest_h1_run(self, scene: dict) -> dict | None:
        if not scene.get("saved"):
            return None
        with self.connect() as db:
            row = db.execute("""
                SELECT json_object('id',p.id,'run_code',json_extract(p.record,'$.run_code'),
                    'status',json_extract(p.record,'$.status'),'input_sha256',json_extract(p.record,'$.input_sha256'),
                    'created_at',json_extract(p.record,'$.created_at'))
                FROM plan_summaries p LEFT JOIN scenes s ON p.scene_id=s.id
                WHERE COALESCE(json_extract(p.record,'$.library_id'),json_extract(s.record,'$.library_id'),s.id)=?
                  AND json_extract(p.record,'$.mode')='h1'
                ORDER BY json_extract(p.record,'$.created_at') DESC LIMIT 1
            """, (scene.get("library_id", scene["id"]),)).fetchone()
        if not row:
            return None
        result = json.loads(row[0])
        result['matches_input'] = result['input_sha256'] == scene['input_sha256']
        return result

    def active_scene_ids(self) -> list[str]:
        with self.connect() as db:
            rows = db.execute("SELECT scene_id FROM plan_summaries WHERE json_extract(record,'$.status') IN ('queued','running')").fetchall()
        return [row[0] for row in rows]

    def recover(self) -> None:
        for header in self.plan_headers():
            if header["status"] in ("queued", "running"):
                record = self.get_plan(header["id"])
                record.update(status="ERROR", certificate=None)
                record.setdefault("progress", []).append({"stage": "interrupted", "message": "Calculation interrupted by service restart; submit a new calculation."})
                self.put_plan(record)

    def expire(self) -> list[dict]:
        expired = []
        now = utcnow()
        active_scenes = set(self.active_scene_ids())
        for scene in self.scenes():
            if scene.get("expires_at") and scene["expires_at"] < now and scene["id"] not in active_scenes:
                with self.connect() as db:
                    db.execute("DELETE FROM plans WHERE scene_id=?", (scene["id"],))
                    db.execute("DELETE FROM scenes WHERE id=?", (scene["id"],))
                expired.append(scene)
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            db.execute("DELETE FROM login_attempts WHERE at<?", (time.time() - 900,))
        return expired

    def login_allowed(self, ip: str) -> bool:
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM login_attempts WHERE at<?", (now - 900,))
            attempts = db.execute("SELECT COUNT(*) FROM login_attempts WHERE ip=?", (ip,)).fetchone()[0]
            global_attempts = db.execute("SELECT COUNT(*) FROM login_attempts WHERE at>?", (now - 60,)).fetchone()[0]
            if attempts >= 5 or global_attempts >= 100:
                return False
            db.execute("INSERT INTO login_attempts VALUES (?,?)", (ip, now))
        return True

    def login_success(self, ip: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM login_attempts WHERE ip=?", (ip,))

    def create_session(self, username: str = "legacy", role: str = "admin") -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES (?,?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), csrf, time.time() + 43200, username, role))
        return token, csrf

    def principal(self, token: str | None) -> dict | None:
        if not token or len(token) > 128:
            return None
        with self.connect() as db:
            row = db.execute("SELECT csrf,username,role FROM sessions WHERE token=? AND expires>?",
                             (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return dict(zip(("csrf_token", "username", "role"), row)) if row else None

    def session(self, token: str | None) -> str | None:
        if not token or len(token) > 128:
            return None
        with self.connect() as db:
            row = db.execute("SELECT csrf FROM sessions WHERE token=? AND expires>?", (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return row[0] if row else None

    def logout(self, token: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),))


def expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
