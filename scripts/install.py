#!/usr/bin/env python3
"""Install the tested compatibility stack, retaining bounded installation logs."""
import argparse
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gmp.api.observability import EventLog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv", type=Path, default=ROOT / ".venv")
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--log-dir", type=Path, default=Path(os.environ.get("GMP_DATA_DIR", ROOT / "artifacts/h3_workspace")) / "logs")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        parser.error("Run with Python 3.12; the tested binary dependencies target this version")
    log = EventLog(args.log_dir, 450000000)
    log.emit("install_start")
    venv.EnvBuilder(with_pip=True).create(args.venv)
    python = args.venv / "bin/python"
    commands = [[str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")]]
    if args.wheelhouse:
        commands[0] += ["--no-index", "--find-links", str(args.wheelhouse.resolve())]
    commands += [[str(python), "-m", "pip", "check"], [str(python), str(ROOT / "scripts/doctor.py")]]
    for index, command in enumerate(commands):
        log.emit("install_step", stage=str(index))
        result = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for line in result.stdout.splitlines():
            log.emit("install_output", stage=str(index), detail=line[:4000])
        log.emit("install_step_finished", stage=str(index), returncode=result.returncode)
        if result.returncode:
            print(f"Installation step {index} failed; see {args.log_dir}", file=sys.stderr)
            return result.returncode
    print(f"Installation verified. Python: {python}. Logs: {args.log_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
