#!/usr/bin/env python3
"""Inspect hardware and validate native libraries in a disposable subprocess."""
import json
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gmp.api.runtime import capacity

if __name__ == "__main__":
    result = capacity()
    probe = subprocess.run([sys.executable, "-c", "import numpy,shapely,rasterio,pyproj,ortools,psycopg; print('native imports OK')"], capture_output=True, text=True)
    result.update(native_imports_ok=probe.returncode == 0, native_exit_code=probe.returncode,
                  python=sys.version.split()[0], tested_python="3.12", target_profile={"cpus": 16, "memory_mb": 65536})
    print(json.dumps(result, indent=2))
    if probe.returncode:
        print("Native library probe failed. Use the pinned compatibility dependencies.", file=sys.stderr)
        sys.exit(1)
