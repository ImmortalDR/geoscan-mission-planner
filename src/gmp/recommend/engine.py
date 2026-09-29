"""Recommendation Engine (TS §18).

When the posed problem cannot be solved, the service must diagnose the cause and
propose verifiable changes. Every candidate is re-planned and re-validated; only
a candidate whose re-plan passes the independent Safety Validator is reported as
``verified``.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any, Sequence

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from ..geo import union_all
from ..kb.catalog import default_kb
from ..models import Plan, Scene, Site, Uav
from ..safety.validator import SAFE
from ..scene_ops import clone_scene, clone_uav, make_site

#: Time budget share of a verification re-plan.
REPLAN_BUDGET_SHARE = 0.5
MAX_REPLAN_BUDGET_S = 40.0


# --------------------------------------------------------------------------- #
def diagnose(
    scene: Scene,
    plan: Plan,
    report,
    capacity: dict[str, Any],
    reach: dict[str, Any],
    dropped: Sequence[str],
) -> dict[str, Any]:
    """Explain *why* the posed mission is not fully solvable."""
    codes: list[str] = []
    details: list[dict[str, Any]] = []

    # Unreachable tasks: is the limit the transit to/from the sites, or the task itself?
    if reach["unreachable_count"]:
        transit_bound = 0
        task_bound = 0
        for item in reach["unreachable"]:
            attempt = item.get("closest_attempt") or {}
            uav = scene.uav(attempt.get("uav", "")) if attempt else None
            task = plan.tasks.get(item["task"])
            if uav is None or task is None:
                continue
            survey_only = task.survey_length_m / max(uav.ground_speed_ms, 0.1)
            if survey_only > uav.usable_endurance_s:
                task_bound += 1
            else:
                transit_bound += 1
        if transit_bound:
            codes.append("reserve_landing_reachability")
            details.append(
                {
                    "code": "reserve_landing_reachability",
                    "message": (
                        f"{transit_bound} atomic task(s) lie beyond the round-trip range of every "
                        "permitted launch/landing site: no site stays reachable with the remaining "
                        "resource, so the area cannot be flown safely from the given sites"
                    ),
                    "examples": reach["unreachable"][:5],
                }
            )
        if task_bound:
            codes.append("task_exceeds_endurance")
            details.append(
                {
                    "code": "task_exceeds_endurance",
                    "message": f"{task_bound} task(s) exceed the usable endurance of every compatible UAV",
                }
            )

    if capacity.get("over_subscribed"):
        codes.append("fleet_capacity_insufficient")
        for sf in capacity["shortfalls"]:
            details.append(
                {
                    "code": "fleet_capacity_insufficient",
                    "payload_class": sf["payload_class"],
                    "message": sf.get("reason", "capacity shortfall"),
                    "required_survey_h": sf.get("required_survey_h"),
                    "available_survey_h_estimate": sf.get("available_survey_h_estimate"),
                    "compatible_uavs": sf.get("compatible_uavs"),
                }
            )

    if dropped and "fleet_capacity_insufficient" not in codes:
        codes.append("mission_window_insufficient")
        details.append(
            {
                "code": "mission_window_insufficient",
                "message": (
                    f"{len(dropped)} atomic task(s) had to be dropped to keep the plan inside the "
                    "mission window"
                ),
            }
        )

    if not plan.metrics.get("coverage_complete", True):
        codes.append("incomplete_coverage")
        details.append(
            {
                "code": "incomplete_coverage",
                "message": (
                    f"achieved coverage {plan.metrics.get('coverage_percent')} % of the effective "
                    "survey area"
                ),
            }
        )

    if plan.metrics.get("wind_excluded_uavs"):
        codes.append("wind_limited_fleet")
        details.append(
            {
                "code": "wind_limited_fleet",
                "message": (
                    f"wind {scene.mission.wind.speed_ms} m/s excludes "
                    f"{plan.metrics['wind_excluded_uavs']} from the mission"
                ),
            }
        )

    for code in report.reasons:
        if code not in codes:
            codes.append(code)
            details.append(
                {
                    "code": code,
                    "message": next(
                        (v.message for v in report.violations if v.code == code), code
                    ),
                }
            )

    return {
        "status": plan.status,
        "reason_codes": codes,
        "details": details,
        "capacity": capacity,
        "reachability": {k: v for k, v in reach.items() if k != "unreachable"},
        "dropped_task_count": len(dropped),
    }


# --------------------------------------------------------------------------- #
def _uncovered_centroids(scene: Scene, plan: Plan, dropped: Sequence[str]) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for tid in dropped:
        task = plan.tasks.get(tid)
        if task is not None:
            c = task.geom.centroid
            pts.append((c.x, c.y))
    if not pts:
        for job in scene.jobs:
            geom = job.effective_geom if job.effective_geom is not None else job.geom
            if geom is not None and not geom.is_empty:
                c = geom.centroid
                pts.append((c.x, c.y))
    return pts


def _safe_site_position(scene: Scene, target: tuple[float, float]) -> tuple[float, float] | None:
    """Nearest position to ``target`` that is inside allowed airspace and clear."""
    allowed = union_all([z.geom for z in scene.allowed_airspace])
    blocked = union_all(
        [z.geom for z in scene.no_fly_zones if z.hard]
        + [o.protected_footprint() for o in scene.obstacles]
        + [j.geom for j in scene.jobs]
    )
    p = Point(target)
    ok = (allowed.is_empty or allowed.contains(p)) and (blocked.is_empty or not blocked.contains(p))
    if ok:
        return target
    for radius in (500.0, 1000.0, 2000.0, 3000.0, 5000.0):
        for k in range(12):
            a = 2.0 * math.pi * k / 12.0
            cand = (target[0] + radius * math.cos(a), target[1] + radius * math.sin(a))
            cp = Point(cand)
            if allowed.is_empty or allowed.contains(cp):
                if blocked.is_empty or not blocked.contains(cp):
                    return cand
    return None


def _verify(
    variant_scene: Scene,
    options,
    label: str,
) -> dict[str, Any]:
    """Re-plan the variant and report the validator verdict."""
    from ..planner import PlannerOptions, plan_mission

    budget = min(MAX_REPLAN_BUDGET_S, max(6.0, options.time_budget_s * REPLAN_BUDGET_SHARE))
    opts = PlannerOptions(
        objective=options.objective,
        time_budget_s=budget,
        cpsat_share=0.3,
        seed=options.seed + 11,
        deconflict=True,
        recommend=False,
    )
    plan = plan_mission(variant_scene, opts)
    independent = (plan.validation or {}).get("checks", {}).get("independent_h3", {})
    verified = plan.status == SAFE and independent.get("passed") is True and bool(plan.certificate)
    return {
        "label": label,
        "status": SAFE if verified else "UNSAFE",
        "input_sha256": independent.get("input_sha256"),
        "result_sha256": independent.get("result_sha256"),
        "certificate": plan.certificate if verified else None,
        "coverage_percent": plan.metrics.get("coverage_percent"),
        "coverage_complete": plan.metrics.get("coverage_complete"),
        "makespan_min": plan.metrics.get("makespan_min"),
        "total_flight_min": plan.metrics.get("total_flight_min"),
        "sorties": plan.metrics.get("sortie_count"),
        "used_uavs": plan.metrics.get("used_uav_count"),
        "certificate_issued": plan.certificate is not None,
        "validator_counters": (plan.validation or {}).get("counters"),
        "dropped_tasks": plan.metrics.get("dropped_task_count"),
        "replan_budget_s": budget,
    }


def _is_better(v: dict[str, Any], baseline: Plan) -> bool:
    if v["status"] != SAFE:
        return False
    base_cov = baseline.metrics.get("coverage_percent") or 0.0
    return (v.get("coverage_percent") or 0.0) >= base_cov - 0.01


# --------------------------------------------------------------------------- #
def recommend(
    scene: Scene,
    plan: Plan,
    diagnosis: dict[str, Any],
    options,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """Build, verify and rank candidate changes to the posed problem."""
    codes = set(diagnosis["reason_codes"])
    dropped = list(plan.metrics.get("dropped_tasks", []))
    candidates: list[dict[str, Any]] = []
    kb = default_kb()

    # ---------- 1. different start site (existing candidate/other sites) ----- #
    if {"reserve_landing_reachability", "task_exceeds_endurance", "mission_window_insufficient",
        "incomplete_coverage", "sortie_resource_exceeded"} & codes:
        current_starts = {u.start_site for u in scene.fleet if u.start_site}
        site_pool = [s for s in scene.sites if s.can_start]
        site_pool.sort(key=lambda s: (0 if s.candidate else 1, s.id))
        for site in site_pool[:3]:
            if site.id in current_starts and len(current_starts) == 1:
                continue
            variant = clone_scene(
                scene,
                scene_id=f"{scene.id}__start_{site.id}",
                start_site_for_all=site.id,
                landing_site_for_all=site.id if site.can_land else None,
                promote_candidates=True,
            )
            ver = _verify(variant, options, f"start_site={site.id}")
            candidates.append(
                {
                    "type": "change_start_site",
                    "title": f"Перенести старт всех БВС на площадку {site.id}",
                    "description": (
                        f"Все вылеты начинаются с площадки {site.id}"
                        + (" (кандидатная площадка из сцены)" if site.candidate else "")
                    ),
                    "rationale": (
                        "исходная стартовая площадка находится за пределами дальности "
                        "возврата для части задач съёмки"
                    ),
                    "changes": {"start_site": site.id, "candidate_site": site.id if site.candidate else None},
                    "candidate": site.id,
                    "verification": ver,
                    "verified": ver["status"] == SAFE,
                }
            )

    # ---------- 2. add a landing / staging site ----------------------------- #
    if {"reserve_landing_reachability", "incomplete_coverage", "mission_window_insufficient"} & codes:
        targets = _uncovered_centroids(scene, plan, dropped)
        if targets:
            cx = sum(p[0] for p in targets) / len(targets)
            cy = sum(p[1] for p in targets) / len(targets)
            pos = _safe_site_position(scene, (cx, cy))
            if pos is not None:
                lon, lat = scene.crs.xy_to_lonlat(*pos)
                new_site = make_site("REC_SITE_1", pos[0], pos[1], role="both")
                variant = clone_scene(
                    scene,
                    scene_id=f"{scene.id}__add_site",
                    extra_sites=[new_site],
                    start_site_for_all="REC_SITE_1",
                )
                ver = _verify(variant, options, "add_landing_site=REC_SITE_1")
                candidates.append(
                    {
                        "type": "add_landing_site",
                        "title": "Добавить посадочную/стартовую площадку вблизи недостижимой части района",
                        "description": (
                            f"Новая площадка REC_SITE_1 в точке {lat:.5f}, {lon:.5f} (WGS-84) "
                            "делает недостижимую часть района съёмки достижимой с учётом резерва"
                        ),
                        "rationale": (
                            "из части контрольных точек маршрута ни одна разрешённая площадка "
                            "не достигается с остатком ресурса"
                        ),
                        "changes": {
                            "new_site": {
                                "id": "REC_SITE_1",
                                "lat": round(lat, 6),
                                "lon": round(lon, 6),
                                "role": "both",
                            }
                        },
                        "candidate": "REC_SITE_1",
                        "verification": ver,
                        "verified": ver["status"] == SAFE,
                    }
                )

    # ---------- 3. add compatible aircraft (fleet composition) -------------- #
    if {"fleet_capacity_insufficient", "incomplete_coverage", "mission_window_insufficient"} & codes:
        shortfalls = diagnosis.get("capacity", {}).get("shortfalls", [])
        extra: list[Uav] = []
        notes: list[str] = []
        for sf in shortfalls[:2]:
            cls = sf["payload_class"]
            template = next((u for u in scene.fleet if u.supports(cls)), None)
            if template is None:
                model = next(
                    (
                        m
                        for m in kb.model_ids()
                        if cls in kb.profile(m).payload_classes
                    ),
                    None,
                )
                notes.append(
                    f"в парке нет носителя для {cls}; в базе знаний подходит модель {model}"
                    if model
                    else f"в базе знаний нет модели с полезной нагрузкой {cls}"
                )
                continue
            required = sf.get("required_survey_h") or 0.0
            available = sf.get("available_survey_h_estimate") or 1e-9
            per_aircraft = available / max(len([u for u in scene.fleet if u.supports(cls)]), 1)
            need = max(1, math.ceil((required - available) / max(per_aircraft, 1e-9)))
            need = min(need, 6)
            for k in range(need):
                extra.append(clone_uav(template, f"{template.id}_EXTRA{k + 1}"))
            notes.append(
                f"{cls}: требуется дополнительно {need} БВС класса {template.model} "
                f"(дефицит {max(required - available, 0):.1f} ч съёмки)"
            )
        if extra:
            variant = clone_scene(
                scene, scene_id=f"{scene.id}__more_uav", extra_fleet=extra
            )
            ver = _verify(variant, options, f"fleet+{len(extra)}")
            candidates.append(
                {
                    "type": "add_uav",
                    "title": f"Увеличить состав парка на {len(extra)} БВС",
                    "description": "; ".join(notes),
                    "rationale": (
                        "суммарный требуемый налёт по отдельным типам съёмки превышает ресурс "
                        "совместимых БВС в заданном временном окне"
                    ),
                    "changes": {
                        "added_uavs": [u.id for u in extra],
                        "models": sorted({u.model for u in extra}),
                    },
                    "verification": ver,
                    "verified": ver["status"] == SAFE and bool(ver.get("coverage_complete")),
                }
            )

    # ---------- 4. extend the mission window / other day -------------------- #
    if {"fleet_capacity_insufficient", "mission_window_insufficient", "incomplete_coverage"} & codes:
        shortfalls = diagnosis.get("capacity", {}).get("shortfalls", [])
        deficit_h = 0.0
        for sf in shortfalls:
            req = sf.get("required_survey_h") or 0.0
            avail = sf.get("available_survey_h_estimate") or 0.0
            n = max(len(sf.get("compatible_uavs") or []), 1)
            deficit_h = max(deficit_h, (req - avail) / n)
        if deficit_h <= 0:
            deficit_h = 2.0
        extra_h = math.ceil(deficit_h * 1.3)
        if scene.mission.window_end is not None:
            variant = clone_scene(
                scene,
                scene_id=f"{scene.id}__window_plus_{extra_h}h",
                window_end=scene.mission.window_end + timedelta(hours=extra_h),
            )
            ver = _verify(variant, options, f"window+{extra_h}h")
            candidates.append(
                {
                    "type": "extend_mission_window",
                    "title": f"Расширить временное окно работ на {extra_h} ч",
                    "description": (
                        f"Окно {scene.mission.window_start:%H:%M}–{scene.mission.window_end:%H:%M} "
                        f"продлевается до "
                        f"{(scene.mission.window_end + timedelta(hours=extra_h)):%H:%M} "
                        "(либо работы переносятся на два дня)"
                    ),
                    "rationale": (
                        "дефицит по времени составляет примерно "
                        f"{deficit_h:.1f} ч на совместимый борт"
                    ),
                    "changes": {"window_end_shift_h": extra_h},
                    "verification": ver,
                    "verified": ver["status"] == SAFE and bool(ver.get("coverage_complete")),
                }
            )

    # ---------- 5. allow different start and landing sites ------------------ #
    if not scene.mission.allow_different_start_end and (
        {"mission_window_insufficient", "incomplete_coverage", "reserve_landing_reachability"} & codes
    ):
        if len([s for s in scene.sites if s.can_land]) > 1:
            variant = clone_scene(
                scene,
                scene_id=f"{scene.id}__diff_start_end",
                allow_different_start_end=True,
            )
            ver = _verify(variant, options, "allow_different_start_end")
            candidates.append(
                {
                    "type": "different_start_end",
                    "title": "Разрешить разные площадки старта и посадки",
                    "description": (
                        "борт садится на ближайшую разрешённую площадку, а не возвращается "
                        "на стартовую"
                    ),
                    "rationale": "возврат на стартовую площадку расходует ресурс, который нужен на съёмку",
                    "changes": {"allow_different_start_end": True},
                    "verification": ver,
                    "verified": ver["status"] == SAFE,
                }
            )

    # ---------- 6. advisory: relax an optional survey parameter -------------- #
    if {"fleet_capacity_insufficient", "incomplete_coverage"} & codes:
        rows = []
        for job in scene.jobs:
            p = scene.payloads.get(job.payload_profile_id)
            if p is None:
                continue
            if p.side_overlap is not None and p.side_overlap > 0.5:
                rows.append(
                    f"{job.id}: боковое перекрытие {p.side_overlap:.0%} -> 50 % увеличивает шаг "
                    f"галсов с {p.swath_spacing_m:.0f} м до "
                    f"{(p.footprint_across_m or p.swath_spacing_m) * 0.5:.0f} м"
                )
            elif p.line_spacing_m:
                rows.append(
                    f"{job.id}: шаг профилей {p.line_spacing_m:.0f} м задан методикой съёмки и "
                    "может быть изменён только решением заказчика"
                )
        candidates.append(
            {
                "type": "relax_survey_parameter",
                "title": "Согласовать с заказчиком ослабление необязательного параметра съёмки",
                "description": "; ".join(rows) or "нет параметров, разрешённых к ослаблению",
                "rationale": (
                    "ТЗ допускает изменение необязательного параметра съёмки только при явном "
                    "разрешении, поэтому рекомендация выдана без автоматической верификации"
                ),
                "changes": {"requires_customer_approval": True},
                "verification": {"status": "NOT_VERIFIED", "note": "требует согласования заказчика"},
                "verified": False,
            }
        )

    # ---------------- rank ------------------------------------------------- #
    def sort_key(rec: dict[str, Any]) -> tuple:
        ver = rec.get("verification") or {}
        cov = ver.get("coverage_percent") or 0.0
        makespan = ver.get("makespan_min") or 1e9
        return (
            0 if rec.get("verified") else 1,
            -cov,
            makespan,
        )

    candidates = [candidate for candidate in candidates if candidate.get("verified")]
    candidates.sort(key=sort_key)
    for i, rec in enumerate(candidates, start=1):
        rec["rank"] = i
    return candidates[:top_k]
