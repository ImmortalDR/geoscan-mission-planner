#!/usr/bin/env python3
"""Export a reviewed source snapshot without inheriting private Git history."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ADDITIONS = ("scripts/prepare_public_repo.py", "scripts/run_local.sh", "docs/PUBLICATION.md")
FORBIDDEN_PARTS = {".git", ".venv", "__pycache__", ".ssh", ".codex", ".cursor", ".cache", "artifacts", "secrets", "build", "dist"}
FORBIDDEN_NAMES = {".gitmodules", "ACCESS.txt", "credentials.txt", "compose.env", "users.json", "db_password", "db_admin_password", "database_url"}
PATTERNS = {
    "private key": rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "GitHub token": rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})",
    "AWS access key": rb"AKIA[0-9A-Z]{16}",
    "API token": rb"sk-[A-Za-z0-9_-]{30,}",
}


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args]).decode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    dest = args.destination.absolute()
    if dest.exists() or dest.is_symlink():
        parser.error("Destination must not exist")
    names = sorted(set(filter(None, git("ls-files", "-z").split("\0"))) | set(ADDITIONS))
    errors = []
    rows = []
    for name in names:
        rel = Path(name)
        p = ROOT / rel
        if rel.is_absolute() or ".." in rel.parts:
            errors.append(f"Invalid path: {name}")
            continue
        if (set(rel.parts) & FORBIDDEN_PARTS or rel.name in FORBIDDEN_NAMES
            or (rel.name.startswith(".env") and rel.name != ".env.example")
            or rel.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".sqlite", ".db"}):
            errors.append(f"Forbidden path: {name}")
            continue
        if not p.is_file() and not p.is_symlink():
            errors.append(f"Missing file or nested repository: {name}")
            continue
        if p.is_symlink():
            target = p.readlink()
            if target.is_absolute() or not p.resolve().is_relative_to(ROOT):
                errors.append(f"External symlink: {name}")
            elif not any((ROOT / n).is_relative_to(p.resolve()) for n in names):
                errors.append(f"Symlink target not included: {name}")
            rows.append({"path": name, "symlink": str(target)})
            continue
        data = p.read_bytes()
        if len(data) >= 50 * 1024 * 1024:
            errors.append(f"File exceeds export size limit: {name}")
        for label, pattern in PATTERNS.items():
            if re.search(pattern, data):
                errors.append(f"Possible {label}: {name}")
        rows.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    if errors:
        parser.exit(1, "\n".join(errors) + "\nNo export created.\n")
    dest.mkdir(parents=True)
    for row in rows:
        target = dest / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "symlink" in row:
            target.symlink_to(row["symlink"])
        else:
            shutil.copy2(ROOT / row["path"], target)
    manifest = {"schema": "geoscan.public-source.v1", "source_commit": git("rev-parse", "HEAD").strip(),
                "source_dirty": bool(git("status", "--porcelain")), "files": rows,
                "note": "SHA-256 describes exported bytes. No inherited Git history. Manifest excludes itself."}
    (dest / "PUBLIC_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"Prepared {len(rows)} entries in {dest}; {sum(r.get('bytes', 0) for r in rows):,} bytes")


if __name__ == "__main__":
    main()
