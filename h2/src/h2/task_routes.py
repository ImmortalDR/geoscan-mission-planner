"""Read H1-authorized variants, with legacy v1 primary-only compatibility."""
def route_variants(task):
    if "route_variants" in task:
        return task["route_variants"]
    coords = []
    for tr in task["transects"]:
        line = tr["coords"]
        coords.extend(line[1:] if coords and coords[-1] == line[0] else line)
    return [dict(id="primary", geom_coords=coords,
                 internal_transition_m=task["internal_transition_m"],
                 fixed_wing_safe=task["fixed_wing_safe"])]
