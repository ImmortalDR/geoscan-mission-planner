#!/usr/bin/env python3
"""Create a secret-free, portable H3 source and Docker build context."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--h1", type=Path, default=REPO / "h1/h1_coverage")
    parser.add_argument("--h2", type=Path, default=REPO / "h2")
    parser.add_argument("--dataset", type=Path, default=REPO / "data")
    parser.add_argument("--evidence", type=Path, help="Verified JSON reports and screenshots, without credentials")
    args = parser.parse_args()
    for path in (args.h1 / "src/h1_coverage", args.h2 / "src/h2", args.dataset / "manifest.json"):
        if not path.exists():
            parser.error(f"Missing source: {path}")
    if args.destination.exists():
        parser.error("Destination must not exist; choose a new version directory")
    dest = args.destination
    dest.mkdir(parents=True)
    default_ignore = shutil.ignore_patterns(".git", ".venv", ".work", "__pycache__", "*.pyc", "*.egg-info",
                                            ".env", ".env.*", ".pytest_cache", ".hypothesis")
    def ignore(directory, names):
        excluded = set(default_ignore(directory, names))
        # The unused legacy PostgreSQL adapter contains a local dev credential.
        if Path(directory) == REPO / "src/gmp/api":
            excluded.add("store.py")
        return excluded
    for rel in ["src", "web/h3", "web/tests", "tests", "scripts", "docs", "datasets", "fixtures", "algorithms", "examples"]:
        if (REPO / rel).exists():
            shutil.copytree(REPO / rel, dest / "app" / rel, ignore=ignore)
    (dest / "app/deploy").mkdir(parents=True)
    for name in ["Dockerfile.h3", "compose.h3.yaml", "geoscan-h3.service",
                 "geoscan-h3-cert-renew.service", "geoscan-h3-cert-renew.timer",
                 "nginx-h3.conf", "install_h3.py", "prepare_h3_bundle.py",
                 "geoscan-mvp.service", "nginx-mvp.conf", "postgres-tls.sh", "postgres-init.sh", "bootstrap.py", "cutover_mvp.py"]:
        shutil.copy2(REPO / "deploy" / name, dest / "app/deploy" / name)
    for name in ["requirements.txt", "requirements.lock", "pyproject.toml", "README.md", "DEMO_PATH.md"]:
        if (REPO / name).exists():
            shutil.copy2(REPO / name, dest / "app" / name)
    shutil.copytree(args.h1 / "src", dest / "h1/src", ignore=ignore)
    shutil.copy2(args.h1 / "pyproject.toml", dest / "h1/pyproject.toml")
    shutil.copytree(args.h2 / "src", dest / "h2/src", ignore=ignore)
    shutil.copy2(args.h2 / "pyproject.toml", dest / "h2/pyproject.toml")
    shutil.copytree(args.dataset, dest / "dataset", ignore=ignore)
    add_legacy_h2_bundles(dest / "dataset")
    if args.evidence:
        for path in sorted(args.evidence.rglob("*")):
            if path.is_file() and path.suffix in {".json", ".png", ".xml"}:
                target = dest / "evidence" / path.relative_to(args.evidence)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    shutil.copy2(REPO / "deploy/Dockerfile.h3", dest / "Dockerfile")
    shutil.copy2(REPO / "deploy/compose.h3.yaml", dest / "compose.yaml")
    shutil.copy2(REPO / "deploy/postgres-tls.sh", dest / "postgres-tls.sh")
    shutil.copy2(REPO / "deploy/postgres-init.sh", dest / "postgres-init.sh")
    nginx = (REPO / "deploy/nginx-h3.conf").read_text()
    nginx = nginx.replace("http://127.0.0.1:8090", "http://app:8090")
    nginx = nginx.replace("/etc/letsencrypt/live/geoscan-h3-ip/", "/etc/h3/")
    (dest / "nginx-container.conf").write_text(nginx)
    files = []
    for path in sorted(dest.rglob("*")):
        if path.is_file():
            files.append({"path":str(path.relative_to(dest)), "sha256":hashlib.sha256(path.read_bytes()).hexdigest(), "bytes":path.stat().st_size})
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True)
    (dest / "MANIFEST.json").write_text(json.dumps({"schema":"geoscan.h3.delivery.v1",
        "release": dest.name, "source_commit": revision.stdout.strip() if revision.returncode == 0 else None,
        "source_dirty": bool(status.stdout.strip()) if status.returncode == 0 else None,
        "pipeline": "h1_coverage -> h2 -> h3", "files":files}, indent=2) + "\n")
    print(f"Prepared {len(files)} files in {dest}")


if __name__ == "__main__":
    main()
