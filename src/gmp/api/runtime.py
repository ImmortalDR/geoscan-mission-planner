"""Conservative resource profiles for one API process and bounded subprocesses."""
from __future__ import annotations

import os
from pathlib import Path


def cgroup_limits(cpus, memory, root=Path("/sys/fs/cgroup"), membership=None):
    try:
        membership = membership if membership is not None else Path("/proc/self/cgroup").read_text()
        relative = next(line.split(":", 2)[2] for line in membership.splitlines() if line.startswith("0::"))
        current = (root / relative.lstrip("/")).resolve()
        root = root.resolve()
        while current == root or root in current.parents:
            try:
                quota, period = (current / "cpu.max").read_text().split()
                if quota != "max":
                    cpus = min(cpus, max(1, int(quota) // int(period)))
            except (OSError, ValueError):
                pass
            try:
                memory = min(memory, int((current / "memory.max").read_text()))
            except (OSError, ValueError):
                pass
            if current == root:
                break
            current = current.parent
    except (OSError, StopIteration):
        pass
    return cpus, memory


def hardware() -> dict:
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    cpus, memory = cgroup_limits(cpus, memory)
    try:
        flags = set(Path("/proc/cpuinfo").read_text().split())
    except OSError:
        flags = set()
    return {"cpus": cpus, "memory_mb": memory // (1024 * 1024),
            "cpu_profile": "modern" if {"avx2", "sse4_2"} <= flags else "compatibility"}


def capacity(machine=None) -> dict:
    machine = machine or hardware()
    recommended = max(1, min(8, machine["cpus"] // 2, (machine["memory_mb"] - 1024) // 2048))
    workers = min(recommended, max(1, int(os.environ.get("GMP_WORKERS", recommended))))
    queue_limit = max(workers, min(64, int(os.environ.get("GMP_QUEUE_LIMIT", max(4, workers * 4)))))
    return {**machine, "workers": workers, "queue_limit": queue_limit,
            "worker_memory_mb": min(4096, max(512, (machine["memory_mb"] - min(1024, machine["memory_mb"] // 4)) // workers))}


def worker_environment() -> dict:
    import sys
    secrets = {"GMP_ACCESS_CODE", "GMP_SESSION_SECRET", "GMP_DATABASE_URL", "GMP_DATABASE_URL_FILE", "GMP_AUTH_FILE", "PGPASSWORD", "GMP_ENCRYPTION_KEY"}
    env = {k: v for k, v in os.environ.items() if k not in secrets and not k.endswith("_TOKEN")}
    paths = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p] + [p for p in sys.path if p]
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[name] = "1"
    env["GMP_WORKER_MEMORY_MB"] = str(capacity()["worker_memory_mb"])
    return env
