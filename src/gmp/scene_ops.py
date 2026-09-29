"""Scene cloning with controlled modifications (used by the what-if re-planner)."""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

from shapely.geometry import Point

from .models import Mission, PayloadProfile, Scene, Site, SurveyJob, Uav


def clone_scene(
    scene: Scene,
    *,
    scene_id: str | None = None,
    extra_sites: Sequence[Site] = (),
    start_site_for_all: str | None = None,
    landing_site_for_all: str | None = None,
    extra_fleet: Sequence[Uav] = (),
    window_end: datetime | None = None,
    window_start: datetime | None = None,
    allow_different_start_end: bool | None = None,
    promote_candidates: bool = False,
    drop_sites: Iterable[str] = (),
) -> Scene:
    """Return an independent copy of the scene with the requested changes.

    Geometry objects are immutable in practice, so they are shared; everything
    the planning pipeline mutates (jobs' derived fields, payload geometry, fleet
    instances) is rebuilt.
    """
    jobs = [
        SurveyJob(
            id=j.id,
            survey_type=j.survey_type,
            payload_profile_id=j.payload_profile_id,
            geom=j.geom,
            properties=dict(j.properties),
        )
        for j in scene.jobs
    ]
    payloads = {
        k: replace(
            v,
            agl_m=0.0,
            gsd_cm_effective=None,
            footprint_across_m=None,
            footprint_along_m=None,
            swath_spacing_m=0.0,
            photo_interval_m=None,
            derivation={},
        )
        for k, v in scene.payloads.items()
    }
    drop = set(drop_sites)
    sites: list[Site] = []
    for s in scene.sites:
        if s.id in drop:
            continue
        sites.append(
            Site(
                id=s.id,
                point=s.point,
                role=s.role,
                candidate=False if promote_candidates else s.candidate,
                elevation_m=s.elevation_m,
                properties=dict(s.properties),
            )
        )
    sites.extend(
        Site(
            id=s.id,
            point=s.point,
            role=s.role,
            candidate=s.candidate,
            elevation_m=s.elevation_m,
            properties=dict(s.properties),
        )
        for s in extra_sites
    )

    fleet: list[Uav] = []
    for u in scene.fleet:
        new = replace(u, notes=list(u.notes))
        if start_site_for_all:
            new = replace(new, start_site=start_site_for_all)
        if landing_site_for_all:
            new = replace(new, landing_site=landing_site_for_all)
        fleet.append(new)
    for u in extra_fleet:
        new = replace(u, notes=list(u.notes))
        if start_site_for_all:
            new = replace(new, start_site=start_site_for_all)
        fleet.append(new)

    mission = replace(
        scene.mission,
        objectives=list(scene.mission.objectives),
        window_start=window_start or scene.mission.window_start,
        window_end=window_end or scene.mission.window_end,
        allow_different_start_end=(
            scene.mission.allow_different_start_end
            if allow_different_start_end is None
            else allow_different_start_end
        ),
        raw=dict(scene.mission.raw),
    )

    return Scene(
        id=scene_id or f"{scene.id}__variant",
        crs=scene.crs,
        jobs=jobs,
        allowed_airspace=list(scene.allowed_airspace),
        no_fly_zones=list(scene.no_fly_zones),
        airspace_constraints=list(scene.airspace_constraints),
        sites=sites,
        obstacles=list(scene.obstacles),
        fleet=fleet,
        payloads=payloads,
        mission=mission,
        dem=scene.dem,
        source_dir=scene.source_dir,
        warnings=[],
        metadata=dict(scene.metadata),
    )


def make_site(site_id: str, x: float, y: float, role: str = "both") -> Site:
    return Site(id=site_id, point=Point(x, y), role=role, properties={"synthesised": True})


def clone_uav(template: Uav, new_id: str, start_site: str | None = None) -> Uav:
    return replace(template, id=new_id, start_site=start_site or template.start_site, notes=list(template.notes))
