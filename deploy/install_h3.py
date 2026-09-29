#!/usr/bin/env python3
"""Install an isolated H3 snapshot without changing the legacy GMP service."""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]


def add_legacy_h2_bundles(dataset_root: Path) -> None:
    """Ship the old H1->H2 bundles beside their corresponding H3 scenarios."""
    source = REPO / "fixtures/h1_h2"
    if not source.is_dir():
        return
    targets = {p.name.split("_", 1)[0]: p for p in dataset_root.glob("scenarios/S*") if p.is_dir()}
    for bundle in source.glob("S*.bundle.json"):
        target = targets.get(bundle.name.split("_", 1)[0])
        if target is not None:
            shutil.copy2(bundle, target / "legacy_h2_bundle.json")
RUNTIME = Path("/opt/geoscan-h3")
STATE = Path("/var/lib/geoscan-h3")
ENV = Path("/etc/geoscan-h3/environment")


def command(*args):
    subprocess.run(args, check=True)


def copy_tree(source, destination):
    shutil.copytree(source, destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".git", ".venv", ".work", "__pycache__", "*.pyc", ".env", ".env.*"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--h1", type=Path, required=True, help="Canonical h1_coverage package root")
    parser.add_argument("--h2", type=Path, required=True, help="Standalone H2 package root")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--no-start", action="store_true")
    parser.add_argument("--skip-dependencies", action="store_true")
    parser.add_argument("--public-origin", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run as root to provision the service account and systemd unit")
    for path in [args.h1 / "src/h1_coverage", args.h2 / "src/h2", args.dataset / "manifest.json", REPO / "web/h3/index.html"]:
        if not path.exists():
            parser.error(f"Missing source: {path}")
    try:
        account = pwd.getpwnam("geoscan-h3")
    except KeyError:
        command("useradd", "--system", "--user-group", "--home-dir", str(STATE), "--shell", "/usr/sbin/nologin", "geoscan-h3")
        account = pwd.getpwnam("geoscan-h3")
    STATE.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chown(STATE, account.pw_uid, account.pw_gid)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    (RUNTIME / "app").mkdir(exist_ok=True)
    # Only deployment-owned trees are replaced; source workspaces stay untouched.
    for source, destination in [(REPO / "src", RUNTIME / "app/src"),
                                (REPO / "fixtures", RUNTIME / "app/fixtures"),
                                (REPO / "web/h3", RUNTIME / "app/web/h3"),
                                (args.h1 / "src", RUNTIME / "h1/src"),
                                (args.h2 / "src", RUNTIME / "h2/src"),
                                (args.dataset, RUNTIME / "dataset")]:
        copy_tree(source, destination)
    add_legacy_h2_bundles(RUNTIME / "dataset")
    shutil.copy2(REPO / "requirements.txt", RUNTIME / "app/requirements.txt")
    python = RUNTIME / "venv/bin/python"
    if not python.exists():
        command(sys.executable, "-m", "venv", str(RUNTIME / "venv"))
    if not args.skip_dependencies:
        command(str(python), "-m", "pip", "install", "-r", str(REPO / "requirements.txt"))
    if not ENV.exists():
        code = getpass.getpass("Shared access code (not written to source): ")
        if not code or any(c in code for c in "\n\r\"'\\ "):
            parser.error("Use a nonempty code without spaces or shell delimiters")
        ENV.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        values = {
            "GMP_ACCESS_CODE": code,
            "GMP_DATA_DIR": str(STATE), "GMP_DATASET_DIR": str(RUNTIME / "dataset"),
            "GMP_WEB_DIR": str(RUNTIME / "app/web/h3"),
            "GMP_PUBLIC_ORIGIN": args.public_origin.rstrip("/"),
            "GMP_SECURE_COOKIES": "1", "GMP_WORKERS": "1", "GMP_RETENTION_DAYS": "30",
        }
        fd = os.open(ENV, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write("".join(f"{key}={value}\n" for key, value in values.items()))
    for name in ["geoscan-h3.service", "geoscan-h3-cert-renew.service", "geoscan-h3-cert-renew.timer"]:
        shutil.copy2(REPO / "deploy" / name, Path("/etc/systemd/system") / name)
    command("systemctl", "daemon-reload")
    command("systemctl", "enable", "geoscan-h3.service", "geoscan-h3-cert-renew.timer")
    if not args.no_start:
        command("systemctl", "restart", "geoscan-h3.service")
        command("systemctl", "start", "geoscan-h3-cert-renew.timer")
    print("Installed isolated H3 snapshot. Legacy gmp.service was not restarted.")
    print("Install deploy/nginx-h3.conf after obtaining the IP certificate, then nginx -t and reload.")


if __name__ == "__main__":
    main()
