#!/usr/bin/env python3
"""Migrate the existing single-server MVP with a private backup and rollback.

Specific to the documented systemd/nginx host. Dry run is the default.
The PostgreSQL database must already exist and belong to geoscan-h3 (peer auth).
"""
import argparse
from contextlib import closing
import json
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import time
from urllib.request import urlopen
import uuid


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout


def switch_link(link, target):
    temporary = link.with_name(link.name + ".next-" + uuid.uuid4().hex)
    temporary.symlink_to(target)
    temporary.replace(link)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    parser.add_argument("venv", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run as the server administrator")
    release, venv = args.release.resolve(), args.venv.resolve()
    manifest = json.loads((release / "MANIFEST.json").read_text())
    if manifest.get("source_dirty") or not manifest.get("source_commit"):
        parser.error("Build a release from a clean committed tree")
    for entry in manifest["files"]:
        path = (release / entry["path"]).resolve()
        if release not in path.parents:
            parser.error("Invalid delivery path")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != entry["sha256"]:
            parser.error(f"Delivery checksum mismatch: {entry['path']}")
    if not (venv / "bin/python").is_file():
        parser.error("Missing tested Python environment")
    source, destination = Path("/var/lib/geoscan-h3"), Path("/var/lib/geoscan-mvp-pg")
    with closing(sqlite3.connect(f"file:{source / 'workspace.sqlite3'}?mode=ro", uri=True)) as db:
        active = db.execute("SELECT COUNT(*) FROM plans WHERE json_extract(record,'$.status') IN ('queued','running')").fetchone()[0]
    if active:
        parser.error("Active calculations exist; wait before cutting over")
    report = {"release": release.name, "source_commit": manifest["source_commit"], "active_before": active, "executed": False}
    if not args.execute:
        print(json.dumps(report))
        return
    backup = Path("/opt/geoscan-mvp/backups") / (time.strftime("%Y%m%d-%H%M%S") + "-sqlite")
    backup.mkdir(parents=True, mode=0o700)
    environment = Path("/etc/geoscan-mvp/environment")
    override = Path("/etc/systemd/system/geoscan-mvp.service.d/reviewer.conf")
    old_environment = environment.read_bytes()
    old_override = override.read_bytes() if override.exists() else None
    current = Path("/opt/geoscan-mvp/current")
    previous = current.resolve()
    shutil.copy2(environment, backup / "environment")
    shutil.copy2("/etc/systemd/system/geoscan-mvp.service", backup / "service.unit")
    if old_override is not None:
        (backup / "reviewer.conf").write_bytes(old_override)
    (backup / "previous-release.txt").write_text(str(previous)+"\n")
    run("systemctl", "stop", "geoscan-mvp.service")
    try:
        shutil.copytree(source, backup / "state")
        migration = run("runuser", "-u", "geoscan-h3", "--", "env", "GMP_DATABASE_URL=postgresql:///geoscan_mvp",
                        str(venv / "bin/python"), str(release / "app/scripts/migrate_postgres.py"),
                        "--source", str(source), "--destination", str(destination), "--execute")
        report["migration"] = json.loads(migration)
        values = {}
        for line in old_environment.decode().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                parts = shlex.split(value)
                values[key] = parts[0] if parts else ""
        values.update(GMP_DATA_DIR=str(destination), GMP_DATABASE_URL="postgresql:///geoscan_mvp",
                      GMP_AUTH_FILE="/etc/geoscan-mvp/users.json", GMP_RELEASE_ID=release.name,
                      GMP_LOG_LIMIT_BYTES="450000000", HOME=str(destination), XDG_CACHE_HOME=str(destination / "cache"))
        temporary = environment.with_suffix(".next")
        temporary.write_text("\n".join(f"{key}={shlex.quote(value)}" for key, value in values.items())+"\n")
        temporary.chmod(0o600)
        temporary.replace(environment)
        override.parent.mkdir(exist_ok=True)
        override.write_text("[Service]\nExecStart=\nExecStart=" + str(venv / "bin/python") +
                            " -m uvicorn gmp.api.app:app --host 127.0.0.1 --port 8092 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1 --no-access-log\n" +
                            f"ReadWritePaths=\nReadWritePaths={destination}\n")
        switch_link(current, release)
        run("systemctl", "daemon-reload")
        run("systemctl", "start", "geoscan-mvp.service")
        for _ in range(60):
            try:
                with urlopen("http://127.0.0.1:8092/health/ready", timeout=2) as response:
                    health = json.load(response)
                if health.get("release") == release.name and health.get("storage") == "postgresql" and health.get("status") == "ready":
                    break
            except OSError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("New service did not become ready")
        report.update(executed=True, health=health, backup=str(backup))
        (backup / "cutover.json").write_text(json.dumps(report, indent=2)+"\n")
        print(json.dumps(report))
    except Exception:
        run("systemctl", "stop", "geoscan-mvp.service")
        environment.write_bytes(old_environment)
        if old_override is None:
            override.unlink(missing_ok=True)
        else:
            override.write_bytes(old_override)
        switch_link(current, previous)
        run("systemctl", "daemon-reload")
        run("systemctl", "start", "geoscan-mvp.service")
        raise RuntimeError(f"Cutover failed; old service restored. Private backup: {backup}") from None


if __name__ == "__main__":
    main()
