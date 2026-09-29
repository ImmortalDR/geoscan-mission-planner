"""H1-owned snake alternatives; survey geometry and line order are immutable."""
import math


def build_route_variants(transects, primary_safe, alternate_safe):
    variants = []
    for flipped in range(2 if len(transects) > 1 else 1):
        lines = [list(reversed(t.coords)) if flipped else list(t.coords) for t in transects]
        coords = []
        for line in lines:
            coords.extend(line[1:] if coords and coords[-1] == line[0] else line)
        variants.append(dict(
            id="alternate" if flipped else "primary",
            geom_coords=[[float(x), float(y)] for x, y in coords],
            internal_transition_m=sum(math.dist(a[-1], b[0]) for a, b in zip(lines, lines[1:])),
            fixed_wing_safe=bool(alternate_safe if flipped else primary_safe),
        ))
    return variants
