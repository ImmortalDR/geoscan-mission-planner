#!/usr/bin/env python3
"""Create or rotate named demo users without passing passwords on the CLI."""
import argparse
import getpass
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gmp.api.auth import ROLES, password_hash


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--file", type=Path, required=True)
    p.add_argument("--username", required=True)
    p.add_argument("--role", choices=sorted(ROLES), required=True)
    args = p.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", args.username):
        p.error("Username must contain 1-64 ASCII letters, digits, dots, underscores or hyphens")
    password = getpass.getpass("Password (at least 12 characters): ")
    if password != getpass.getpass("Repeat password: "):
        p.error("Passwords do not match")
    encoded = password_hash(password)
    users = json.loads(args.file.read_text()) if args.file.exists() else {"users": {}}
    users["users"][args.username] = {"role": args.role, "password_hash": encoded}
    args.file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = args.file.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(users, stream, indent=2)
        stream.write("\n")
    temporary.replace(args.file)
    args.file.chmod(0o600)
    print("User updated. Restart the service to revoke existing sessions and apply roles.")


if __name__ == "__main__":
    main()
