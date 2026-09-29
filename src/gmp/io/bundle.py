"""Removed: H1H2Bundle export lives in h1_coverage.bundle.

Use: ``h1-coverage run`` or ``from h1_coverage.pipeline import run_h1_to_file``.
"""

SCHEMA_VERSION = "gmp.h1_h2.v1"


def export_h1_h2_bundle(*_a, **_k):  # noqa: ANN001
    raise RuntimeError(
        "gmp.io.bundle removed — use package h1_coverage (h1/h1_coverage)"
    )


def validate_bundle_dict(*_a, **_k):  # noqa: ANN001
    raise RuntimeError("gmp.io.bundle removed — use h1_coverage.bundle.validate_bundle")


def build_feasibility(*_a, **_k):  # noqa: ANN001
    raise RuntimeError("gmp.io.bundle removed — use h1_coverage.feasibility.build_feasibility")
