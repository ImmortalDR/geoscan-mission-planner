import os
import subprocess
import sys

import pytest

from gmp.api.worker_process import BoundedProcess


def test_large_worker_output_is_drained_and_bounded():
    worker = BoundedProcess([sys.executable, "-c", "import sys; sys.stdout.write('a'*2000000); sys.stderr.write('b'*2000000)"], env=os.environ.copy())
    stdout, stderr = worker.communicate(timeout=10)
    assert worker.returncode == 0
    assert stdout == "a" * 65536 and stderr == "b" * 65536


def test_worker_timeout_can_be_cancelled():
    worker = BoundedProcess([sys.executable, "-c", "import time; time.sleep(60)"], env=os.environ.copy())
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            worker.communicate(timeout=.05)
    finally:
        worker.kill()
        worker.communicate()
    assert worker.returncode != 0


def test_exception_in_parent_reaps_worker():
    with pytest.raises(RuntimeError, match="database unavailable"):
        with BoundedProcess([sys.executable, "-c", "import time; time.sleep(60)"], env=os.environ.copy()) as worker:
            raise RuntimeError("database unavailable")
    assert worker.poll() is not None
