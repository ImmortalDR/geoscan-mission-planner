"""Roundoff-safe row counts without changing the sweep's fixed spacing."""
import math


def stable_ceil_ratio(width: float, spacing: float) -> int:
    """Do not add a row for floating-point noise just above an integer."""
    if width <= 0 or spacing <= 0:
        raise ValueError('Positive width and spacing are required')
    ratio = width / spacing
    integer = round(ratio)
    if math.isclose(ratio, integer, rel_tol=1e-10, abs_tol=1e-10):
        ratio = float(integer)
    return max(1, math.ceil(ratio))


def fixed_spacing_rows(lower: float, upper: float, spacing: float) -> tuple[int, float, float]:
    """Original fixed-pitch lattice, with a stable interior phase at integers.

    Previously a ratio infinitesimally above an integer placed the retained
    rows half a pitch inside the boundary, while an exact/below-integer ratio
    put rows on the boundary. Use the interior phase consistently for numerical
    ties. Non-integer widths keep the original count, pitch and offset.
    """
    width = upper - lower
    count = stable_ceil_ratio(width, spacing)
    ratio = width / spacing
    integer = round(ratio)
    if integer >= 1 and math.isclose(ratio, integer, rel_tol=1e-10, abs_tol=1e-10):
        return integer, spacing, lower + (width - (integer - 1) * spacing) / 2
    if width < spacing:
        return 1, spacing, lower + width / 2
    count += 1
    return count, spacing, lower + (width - (count - 1) * spacing) / 2


def centered_rows(lower: float, upper: float, spacing: float) -> tuple[int, float, float]:
    """Return count, pitch and first centre, with pitch no larger than spacing
    apart from floating-point roundoff. Near-integer ratios are snapped before
    ceil; coverage is still independently checked using the physical swaths.
    """
    width = upper - lower
    count = stable_ceil_ratio(width, spacing)
    pitch = width / count
    return count, pitch, lower + pitch / 2
