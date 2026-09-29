"""Read secret files without exposing connection strings to subprocesses or logs."""
import os
from pathlib import Path


def database_url():
    secret_file = os.environ.get("GMP_DATABASE_URL_FILE")
    value = Path(secret_file).read_text().strip() if secret_file else os.environ.get("GMP_DATABASE_URL", "")
    if value and os.environ.get("GMP_DEPLOYMENT_MODE") == "internal":
        from psycopg.conninfo import conninfo_to_dict
        config = conninfo_to_dict(value)
        local_socket = not config.get("host") or config["host"].startswith("/")
        if not local_socket and config.get("sslmode") != "verify-full":
            raise ValueError("Internal network database connections require sslmode=verify-full")
    return value
