"""Meta: every registered module has a contract checker + test file."""
from __future__ import annotations

from pathlib import Path

from h1_coverage.contracts import CHECKERS
from h1_coverage.modules import MODULES, contract_ids


def test_all_modules_registered():
    assert [m.id for m in MODULES] == [f"M{i}" for i in range(1, 14)]
    assert len(contract_ids()) == 13
    for m in MODULES:
        assert m.id in CHECKERS
        test_path = Path(__file__).parent / Path(m.pytest_node).name
        assert test_path.is_file(), f"missing {test_path}"
