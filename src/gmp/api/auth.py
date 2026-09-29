"""Named demo identities and role checks; passwords never enter sessions."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

ROLES = {"viewer", "planner", "admin"}
ITERATIONS = 600000


def password_hash(password: str) -> str:
    if not 12 <= len(password) <= 256:
        raise ValueError("Password must contain 12 to 256 characters")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    if not isinstance(password, str) or len(password) > 256:
        return False
    try:
        scheme, rounds, salt, expected = encoded.split("$")
        if scheme != "pbkdf2_sha256" or not 600000 <= int(rounds) <= 1200000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


class Authentication:
    def __init__(self):
        self.public = os.environ.get("GMP_PUBLIC_ACCESS") == "1"
        self.code = os.environ.get("GMP_ACCESS_CODE", "")
        path = os.environ.get("GMP_AUTH_FILE")
        self.users = json.loads(Path(path).read_text())["users"] if path else {}
        for name, user in self.users.items():
            if not name or len(name) > 64 or user.get("role") not in ROLES or not isinstance(user.get("password_hash"), str):
                raise ValueError("Invalid authentication file")
        if os.environ.get("GMP_DEPLOYMENT_MODE") == "internal" and (self.public or self.code or not self.users):
            raise ValueError("Internal deployment requires named users and no shared access code")
        self.dummy = password_hash(secrets.token_urlsafe(32)) if self.users else ""

    @property
    def configured(self):
        return bool(self.public or self.code or self.users)

    @property
    def version(self):
        settings = {"code": self.code, "users": self.users}
        if self.public:
            settings["public"] = True
        return json.dumps(settings, sort_keys=True)

    def authenticate(self, body):
        username = body.get("username", "")
        if username:
            if not isinstance(username, str):
                return None
            user = self.users.get(username)
            valid = verify_password(body.get("password", ""), user["password_hash"] if user else self.dummy)
            return {"username": username, "role": user["role"]} if user and valid else None
        code = body.get("code", "")
        if self.code and isinstance(code, str) and hmac.compare_digest(code.encode(), self.code.encode()):
            return {"username": "legacy", "role": "admin"}
        return None


def permitted(role: str, method: str, path: str) -> bool:
    if path.startswith("/api/v1/admin/"):
        return role == "admin"
    if method in {"GET", "HEAD", "OPTIONS"} or path == "/api/v1/auth/logout":
        return True
    if role == "admin":
        return True
    return role == "planner" and method != "DELETE" and not path.startswith("/api/v1/admin/")
