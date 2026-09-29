#!/usr/bin/env python3
"""Generate private demo credentials and a local TLS configuration for Compose."""
import argparse
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gmp.api.auth import password_hash
from gmp.api.runtime import hardware


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path, help="New private directory outside versioned source")
    args = p.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    users = {role: secrets.token_urlsafe(24) for role in ("viewer", "planner", "admin")}
    (directory / "users.json").write_text(json.dumps({"users": {name:{"role":name,"password_hash":password_hash(password)} for name,password in users.items()}},indent=2))
    (directory / "credentials.txt").write_text("\n".join(f"{name}: {password}" for name,password in users.items())+"\n")
    db_password = secrets.token_urlsafe(32)
    (directory / "db_password").write_text(db_password)
    (directory / "db_admin_password").write_text(secrets.token_urlsafe(32))
    (directory / "database_url").write_text(f"postgresql://geoscan:{db_password}@db/geoscan?sslmode=verify-full&sslrootcert=/run/secrets/db_ca")
    for name, subject, san in (("db", "db", "DNS:db"), ("web", "localhost", "DNS:localhost,IP:127.0.0.1")):
        subprocess.run(["openssl","req","-x509","-newkey","rsa:3072","-nodes","-sha256","-days","30","-subj",f"/CN={subject}","-addext",f"subjectAltName={san}","-keyout",str(directory/f"{name}.key"),"-out",str(directory/f"{name}.crt")],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    for file in directory.iterdir():
        file.chmod(0o600 if file.name=="credentials.txt" else 0o444)
    machine = hardware()
    values={"GMP_PUBLIC_ORIGIN":"https://localhost","GMP_AUTH_FILE_HOST":str(directory/"users.json"),"GMP_DATABASE_URL_HOST":str(directory/"database_url"),"DB_PASSWORD_FILE":str(directory/"db_password"),"DB_CERT_FILE":str(directory/"db.crt"),"DB_KEY_FILE":str(directory/"db.key"),"TLS_CERT_FILE":str(directory/"web.crt"),"TLS_KEY_FILE":str(directory/"web.key"),"GMP_MEMORY_LIMIT":f"{max(1024,int(machine['memory_mb']*.65))}m","GMP_CPU_LIMIT":str(min(16,machine["cpus"]))}
    values["DB_ADMIN_PASSWORD_FILE"] = str(directory / "db_admin_password")
    env = directory / "compose.env"
    env.write_text("\n".join(f"{key}={value}" for key,value in values.items())+"\n")
    env.chmod(0o600)
    print(f"Compose environment: {env}\nNamed-user credentials: {directory / 'credentials.txt'}\nLocal demo certificates require explicit browser trust; use the corporate CA for deployment.")


if __name__ == "__main__":
    main()
