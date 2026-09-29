"""Compare the local H1 bundle copies with an optional live H1 directory."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--live",type=Path)
    args=p.parse_args()
    folder=ROOT/"fixtures"
    local={path.name:path for path in folder.glob("*.bundle.json")}
    errors=[]
    if args.live:
        live={path.name:path for path in args.live.glob("*.bundle.json")}
        missing = set(live) - set(local)
        if missing:
            errors.extend(f"missing official bundle: {name}" for name in sorted(missing))
        for name in set(local) & set(live):
            if hashlib.sha256(local[name].read_bytes()).digest() != hashlib.sha256(live[name].read_bytes()).digest():
                errors.append(name)
    print(json.dumps(dict(passed=not errors,bundles=len(local),official=len(live) if args.live else None,
                          mismatches=errors),indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
