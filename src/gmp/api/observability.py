"""Structured logs with a shared on-disk byte budget and oldest-first retention."""
from __future__ import annotations

import fcntl
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path


class EventLog:
    def __init__(self, root: Path, limit_bytes: int = 500_000_000):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.limit = max(1024, min(500_000_000, limit_bytes))
        self.lock = threading.RLock()

    @contextmanager
    def locked(self):
        with self.lock, (self.root / ".lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _prune(self, incoming=0, keep=None):
        files = [p for p in self.root.rglob("*") if p.is_file() and not p.is_symlink() and p.name != ".lock" and p.suffix != ".tmp"]
        total = sum(p.stat().st_size for p in files)
        for path in sorted(files, key=lambda p: (p.stat().st_mtime_ns, str(p))):
            if total + incoming <= self.limit:
                break
            if path == keep:
                continue
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            total -= size
        return total + incoming <= self.limit

    def emit(self, event: str, **fields):
        # Only explicitly selected metadata is accepted; no request bodies, headers or DSNs.
        allowed = {"request_id", "method", "route", "status", "duration_ms", "username", "role", "plan_id", "error_type", "stage", "release", "workers", "returncode", "detail"}
        record = {"at": time.time(), "event": event, **{k: v for k, v in fields.items() if k in allowed}}
        if "detail" in record:
            record["detail"] = re.sub(r"(://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", str(record["detail"]))
        payload = (json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n").encode()
        if len(payload) > min(self.limit, 65536):
            return
        with self.locked():
            path = self.root / "service.jsonl"
            if path.exists() and path.stat().st_size + len(payload) > min(5_000_000, self.limit // 2):
                path.rename(self.root / f"service-{time.time_ns()}.jsonl")
            if self._prune(len(payload), keep=path):
                with path.open("ab") as handle:
                    handle.write(payload)
                path.chmod(0o600)

    def prune(self):
        with self.locked():
            self._prune()

    def write_json(self, path: Path, value: dict):
        path = path.resolve()
        if self.root not in path.parents:
            return
        payload = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
        if len(payload) > self.limit:
            return
        with self.locked():
            if not self._prune(len(payload), keep=path):
                return
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = path.with_suffix(path.suffix + ".tmp")
            try:
                temporary.write_bytes(payload)
                temporary.chmod(0o600)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
