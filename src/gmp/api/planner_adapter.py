"""Consume canonical H1/H2 without changing their models or geometry."""

from __future__ import annotations

import importlib.util
import json
import time
from dataclasses import asdict
from pathlib import Path


def load_dataset_module(dataset: Path, name: str):
    spec = importlib.util.spec_from_file_location(f"gmp_h3_{name}", dataset / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"H3 dataset module unavailable: {name}")
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def transfer_coverage(scene, result):
    """Translate canonical H1 output without relying on private bridge helpers."""
    from shapely.geometry import LineString
    from gmp.coverage.engine import CoverageResult
    from gmp.models import AtomicTask, Transect

    for profile_id, source in result.scene.payloads.items():
        target = scene.payloads[profile_id]
        for name in ("agl_m", "footprint_across_m", "footprint_along_m", "swath_spacing_m"):
            setattr(target, name, getattr(source, name))
        if target.camera is not None:
            target.gsd_cm_effective = target.camera.gsd_for_agl(target.agl_m) * 100.0
        if target.footprint_along_m is not None and target.front_overlap is not None:
            target.photo_interval_m = target.footprint_along_m * (1.0 - target.front_overlap)
        target.derivation = {"source": "canonical H1 with explicit input camera"}

    jobs = {job.id: job for job in result.scene.jobs}
    for target in scene.jobs:
        target.effective_geom = jobs[target.id].effective_geom

    tasks = {}
    for task_id, source in result.coverage.tasks.items():
        transects = [Transect(coords=list(t.coords), length_m=t.length_m, job_id=t.job_id)
                     for t in source.transects]
        tasks[task_id] = AtomicTask(
            id=source.id, job_id=source.job_id, payload_class=source.payload_class,
            payload_profile_id=source.payload_profile_id, agl_m=source.agl_m,
            transects=transects, survey_length_m=source.survey_length_m,
            turn_count=source.turn_count, sweep_angle_deg=source.sweep_angle_deg,
            entry=tuple(source.entry), exit=tuple(source.exit),
            geom=LineString(source.geom_coords or transects[0].coords),
            internal_transition_m=source.internal_transition_m,
            fixed_wing_safe=source.fixed_wing_safe, notes=list(source.notes or []),
            route_variants=source.route_variants,
        )
    coverage = result.coverage
    return CoverageResult(
        tasks=tasks, per_job=list(coverage.per_job), exclusions=dict(coverage.exclusions),
        payloads=[asdict(profile) for profile in result.scene.payloads.values()],
        candidates=dict(coverage.candidates), coverage_percent=float(coverage.coverage_percent),
        warnings=list(coverage.warnings),
    )


def _emit_progress(progress_file: str | None, stage: str, status: str, message: str,
                   started_at: float | None = None, finished_at: float | None = None, extra: dict | None = None) -> None:
    """Write small, append-only stage events consumed by the API worker."""
    if not progress_file:
        return
    event = {"stage": stage, "status": status, "message": message, "at": time.time()}
    if started_at is not None:
        event["started_at"] = started_at
    if finished_at is not None:
        event["finished_at"] = finished_at
        if started_at is not None:
            event["duration_s"] = max(0.0, finished_at - started_at)
    if extra:
        event.update(extra)
    path = Path(progress_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def _h1_geojson(bundle: dict) -> dict:
    """Expose the old viewer's task envelopes and transects in WGS84.

    ``job_id`` remains an internal H1/H2 compatibility identifier; the UI
    presents the mission by sensor type instead. ``task_id`` is the atomic
    unit H2 receives. H1 does not assign a UAV here.
    """
    from pyproj import Transformer
    from shapely.geometry import MultiPoint

    transformer = Transformer.from_crs(bundle["crs"]["metric_epsg"], "EPSG:4326", always_xy=True)
    features = []
    palette = ["#087c70", "#d17a24", "#427cbd", "#b64976", "#8464ac", "#76932a", "#c1524b", "#367e95", "#956843", "#575fba"]
    sensors = {task.get("payload_class") for task in bundle.get("tasks", [])}
    sensor_colors = {sensor: palette[index % len(palette)] for index, sensor in enumerate(sorted(sensors, key=str))}

    # Use one normal per job, independent of alternating flight directions.
    # Subtract a local origin so absolute UTM coordinates cannot inflate spacing.
    offsets_by_job = {}
    frames_by_job = {}
    for task in bundle.get("tasks", []):
        for transect in task.get("transects", []):
            coords = transect.get("coords", [])
            if len(coords) < 2:
                continue
            a, b = coords[0], coords[-1]
            length = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
            if length < 1e-9:
                continue
            job_id = task.get("job_id")
            origin, normal = frames_by_job.setdefault(
                job_id, (a, (-(b[1] - a[1]) / length, (b[0] - a[0]) / length)))
            midpoint = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            offsets_by_job.setdefault(job_id, []).append(
                (midpoint[0] - origin[0]) * normal[0] + (midpoint[1] - origin[1]) * normal[1])
    pitch_by_job = {}
    for job, offsets in offsets_by_job.items():
        unique = sorted({round(value, 6) for value in offsets})
        gaps = [right - left for left, right in zip(unique, unique[1:]) if right - left > 1e-3]
        pitch_by_job[job] = min(gaps) if gaps else 24.0

    def lonlat(point):
        return list(transformer.transform(point[0], point[1]))

    for task in bundle.get("tasks", []):
        task_id, job_id = task.get("id"), task.get("job_id")
        sensor = task.get("payload_class")
        color = sensor_colors.get(sensor, palette[0])
        transects = task.get("transects", [])
        properties = {"task_id": task_id, "job_id": job_id, "color": color,
                      "payload_class": task.get("payload_class"),
                      "payload_profile_id": task.get("payload_profile_id"),
                      "transect_count": len(transects),
                      "survey_length_m": task.get("survey_length_m", 0)}
        points = []
        for index, transect in enumerate(transects):
            coords = [lonlat(point) for point in transect.get("coords", [])]
            points.extend(transect.get("coords", []))
            if len(coords) >= 2:
                features.append({"type": "Feature", "properties": {**properties, "kind": "transect", "index": index},
                                 "geometry": {"type": "LineString", "coordinates": coords}})
        if len(points) < 2:
            continue
        # Exactly one buffered convex envelope for every AtomicTask, as in H2 viewer.
        hull = MultiPoint(points).convex_hull
        offset_m = max(1.0, pitch_by_job.get(job_id, 24.0) / 4.0)
        envelope = hull.buffer(offset_m)
        ring = [lonlat(point) for point in envelope.exterior.coords]
        features.append({"type": "Feature", "properties": {**properties, "kind": "task_envelope",
                                                               "offset_m": offset_m,
                                                               "label": f"AtomicTask {task_id}"},
                         "geometry": {"type": "Polygon", "coordinates": [ring]}})
        centroid = envelope.centroid
        features.append({"type": "Feature", "properties": {"task_id": task_id, "job_id": job_id,
                                                               "color": color, "kind": "task_label",
                                                               "label": f"{task_id} · {len(transects)} галсов"},
                         "geometry": {"type": "Point", "coordinates": lonlat((centroid.x, centroid.y))}})
    return {"type": "FeatureCollection", "features": features, "envelope_version": 2}


def refresh_h1_view(record: dict) -> None:
    """Repair derived display geometry when opening older saved runs.

    The original H1 bundle, run logs, metrics and timing remain unchanged.
    Only the API response is updated, never the archived calculation.
    """
    result = record.get("plan") or {}
    bundle = result.get("h1_bundle")
    view = result.get("h1_output")
    if not bundle or not view:
        return
    if view.get("geojson", {}).get("envelope_version") != 2:
        view["geojson"] = _h1_geojson(bundle)
    for event in record.get("progress", []):
        if isinstance(event, dict) and event.get("stage") == "h1" and event.get("h1_output"):
            event["h1_output"]["geojson"] = view["geojson"]


def build_h1(input_dir: Path, dataset: Path, objective: str, progress_file: str | None = None) -> dict:
    """Run canonical H1 only; no scheduling or safety certificate is produced."""
    from h1_coverage.config import CoverageConfig
    from h1_coverage.pipeline import run_h1_scene
    from gmp.api.h2_adapter import apply_h2_site_preferences, prepare_h1_fleet

    started = time.time()
    _emit_progress(progress_file, "h1", "running", "H1: построение галсов и задач", started)
    adapter = load_dataset_module(dataset, "reference_builder")
    _, scene, _ = adapter._prepare_scenes(input_dir, objective)
    prepare_h1_fleet(scene, input_dir)
    options = json.loads((input_dir / "metadata.json").read_text()).get("reference_generation", {})
    result = run_h1_scene(scene, CoverageConfig(strict_coverage=False, coverage_pass_percent=100.0,
                          angle_step_deg=float(options.get("angle_step_deg", 15))))
    bundle = result.bundle.data
    apply_h2_site_preferences(bundle, input_dir)
    view = {"schema": "geoscan.h1.viewer.v1", "scene_id": bundle["scene_id"],
            "crs": bundle["crs"], "task_count": len(bundle["tasks"]), "geojson": _h1_geojson(bundle)}
    _emit_progress(progress_file, "h1", "completed", f"H1 завершён: {len(bundle['tasks'])} задач",
                   started, time.time(), {"h1_output": view})
    return {"status": "COMPLETED", "h1_output": view, "h1_bundle": bundle,
            "provenance": {"pipeline": "h1_coverage", "h1_executed": True,
                           "h2_executed": False, "h3_executed": False}}


def build_live(input_dir: Path, dataset: Path, objective: str, budget: float, seed: int,
               progress_file: str | None = None, search_depth: str = "deep") -> dict:
    from h2.contract import fingerprint
    from h2.planner import plan_bundle
    from h2.scheduler import Settings
    from h1_coverage.config import CoverageConfig
    from h1_coverage.pipeline import run_h1_scene
    from gmp.api.h2_adapter import apply_h2_site_preferences, build_scene_sidecar, digest, export_sorties, prepare_h1_fleet
    from gmp.safety.h3_gate import infer_infeasibility, input_fingerprint, recompute_metrics

    input_dir, dataset = Path(input_dir), Path(dataset)
    input_hash, source_hashes = input_fingerprint(input_dir)
    refusal = infer_infeasibility(input_dir, objective)
    if refusal is not None:
        refusal["provenance"] = {
            "mode": "live", "generator": "H3 necessary-condition proof before H1/H2 planning",
            "input_sha256": input_hash, "input_files_sha256": source_hashes,
            "h1_executed": False, "h2_executed": False,
        }
        return refusal
    adapter = load_dataset_module(dataset, "reference_builder")
    _, h1_scene, profiles = adapter._prepare_scenes(input_dir, objective)
    prepare_h1_fleet(h1_scene, input_dir)
    options = json.loads((input_dir / "metadata.json").read_text()).get("reference_generation", {})
    config = CoverageConfig(strict_coverage=False, coverage_pass_percent=100.0,
                            angle_step_deg=float(options.get("angle_step_deg", 15)))
    h1_started = time.time()
    _emit_progress(progress_file, "h1", "running", "H1: строится покрытие и формируется набор галсов", h1_started)
    h1_result = run_h1_scene(h1_scene, config)
    h1_finished = time.time()
    bundle = h1_result.bundle.data
    apply_h2_site_preferences(bundle, input_dir)
    h1_view = {"schema": "geoscan.h1.viewer.v1", "scene_id": bundle["scene_id"],
               "crs": bundle["crs"], "task_count": len(bundle.get("tasks", [])),
               "geojson": _h1_geojson(bundle)}
    _emit_progress(progress_file, "h1", "completed", f"H1 завершён: {len(bundle.get('tasks', []))} задач",
                   h1_started, h1_finished, {"h1_output": h1_view})
    from h1_coverage.transect_bundle import export_transect_bundle
    bundle = export_transect_bundle(bundle)
    bundle_hash = fingerprint(bundle)
    sidecar = build_scene_sidecar(input_dir, bundle)
    payloads = json.loads((input_dir / "payload_catalog.json").read_text())["payload_profiles"]
    speed_factor = min(float(profile["survey_speed_factor"]) for profile in payloads)
    settings = Settings(algorithm="routing", search_depth=search_depth, objective=objective, time_budget_s=budget, seed=seed,
                        sample_step_m=20.0, survey_speed_factor=speed_factor)
    h2_started = time.time()
    _emit_progress(progress_file, "h2", "running", "H2: распределяются задачи и строится расписание", h2_started)
    stage_names = {"graph_precomputation": "Построение графа галсов", "graph_profile": "Расчёт рёбер для профиля БПЛА", "graph_ready": "Граф и ресурсные таблицы готовы", "routing_search": "Оптимизация OR-Tools Routing", "routing_expand_reloads": "Расширение числа промежуточных обслуживаний", "routing_diagnostic": "Полный план не найден: поиск частичного решения и причин", "precomputation": "Подготовка таблиц перелётов и возврата", "initial_plan": "Начальный план H2",
                   "annealing": "Имитация отжига", "routing_blocks": "Упорядочивание и обмен блоками галсов", "full_validation": "Проверка лучших планов и исправление расписания",
                   "routing_windows": "Планирование оставшихся галсов по разрешённым окнам",
                   "complete": "Выбран итоговый план H2"}
    def search_progress(event):
        stage = event.get("stage")
        message = stage_names.get(stage, stage)
        if stage == "routing_windows":
            message += f": окно {event['window']} из {event['windows']}, осталось {event['remaining']}"
        if event.get("objective_value") is not None:
            message += f": {event['objective_value']/60:.1f} мин; неназначено: {event.get('unassigned_count', 0)}"
        _emit_progress(progress_file, "h2", "running", message, extra={"search_progress": event})
    plan = plan_bundle(bundle, settings=settings, scene=sidecar, progress=search_progress)
    h2_finished = time.time()
    h2_sorties = export_sorties(bundle, plan)
    h2_view = {"schema": "geoscan.h2.viewer.v1", "status": plan.get("status"),
               "sorties": h2_sorties, "metrics": plan.get("metrics", {}),
               "task_count": len(plan.get("tasks", [])), "unassigned": plan.get("unassigned", []),
               "routing_graph": plan.get("routing_graph"), "routing": plan.get("routing")}
    _emit_progress(progress_file, "h2", "completed", f"H2: назначено {len(bundle['tasks']) - len(plan['unassigned'])} из {len(bundle['tasks'])} галсов; {len(plan['sorties'])} вылетов",
                   h2_started, h2_finished, {"h2_output": h2_view})
    if fingerprint(bundle) != bundle_hash or plan["tasks"] != bundle["tasks"]:
        raise ValueError("H2 changed immutable canonical H1 task data")
    if input_fingerprint(input_dir)[0] != input_hash:
        raise ValueError("Source inputs changed while the pipeline was running")
    result = {
        "schema": "geoscan.h3.result.v1", "scene_id": bundle["scene_id"],
        # Only a complete candidate may claim SAFE for the independent gate.
        "objective": objective,
        "status": "SAFE" if plan["status"] == "FEASIBLE" and plan["checks"]["passed"] else "UNSAFE",
        "optimality": {"status": "unknown"},
        "sorties": h2_sorties,
        "h1_output": h1_view,
        "h1_bundle": bundle,
        "h2_output": h2_view,
        "metrics": {},
        "diagnosis": {"h2_status": plan["status"], "unassigned": plan["unassigned"],
                      "h2_checks": plan["checks"], "requires_h3_validation": True},
        "provenance": {
            "mode": "live", "generator": "canonical h1_coverage.run_h1_scene -> standalone h2.planner.plan_bundle -> independent H3 gate",
            "pipeline": "h1_coverage -> h2 -> h3", "planner_module": "h2.planner",
            "h2_input_sha256": plan["input_sha256"],
            "candidate_requires_independent_check": True,
            "candidate_service_status": plan["status"], "candidate_service_reasons": plan["checks"].get("violations", []),
            "input_sha256": input_hash, "input_files_sha256": source_hashes,
            "h1_bundle_schema": bundle["schema_version"], "h1_bundle_sha256": bundle_hash,
            "h1_task_geometry_sha256": digest(bundle["tasks"]),
            "h2_plan_schema": plan["schema_version"], "h2_plan_sha256": digest(plan),
            "h2_status": plan["status"], "h2_checks": plan["checks"],
            "h2_unassigned": plan["unassigned"], "h2_settings": asdict(settings), "annealing": plan.get("annealing"), "routing": plan.get("routing"),
            "h2_solver_log": plan["solver_log"], "h2_deconfliction": plan["deconfliction"],
            "h2_assumptions": plan["assumptions"] + sidecar["assumptions"],
            "seed": seed, "planner_time_budget_s": budget, "waypoint_step_m": 20,
            "waypoint_phase_semantics": "incoming segment", "position_interpolation": "linear metric X/Y and AMSL between timestamps",
            "dem_sampling": "containing raster cell", "metric_crs": bundle["crs"]["metric_epsg"],
            "planning_profiles": profiles, "h1_task_count": len(bundle["tasks"]), "h1_config": asdict(config),
            "h1_warnings": h1_result.coverage.warnings,
        },
    }
    if result["status"] != "SAFE":
        from collections import Counter
        assigned = len({task for sortie in plan["sorties"] for task in sortie["task_ids"]})
        missing = len(plan["unassigned"])
        reason_labels={
            'graph_resource_limit':'недостаточный ресурс с запасом возврата в рассчитанном графе',
            'no_verified_graph_path':'нет проверенного пути от разрешённых баз',
            'no_eligible_uav':'нет совместимого БПЛА',
            'routing_search_unresolved':'отдельно доступны, но общий план пока не найден',
        }
        reason_counts=Counter(item.get('reason','unknown') for item in plan['unassigned'])
        reason_summary='; '.join(f'{count} — {reason_labels.get(reason,reason)}' for reason,count in sorted(reason_counts.items()))
        result["diagnosis"].update({
            "assigned_task_count": assigned, "unassigned_task_count": missing,
            "unassigned_reason_counts":dict(reason_counts),
            "message": (f"Не удалось подтвердить полный план. Назначено задач: {assigned}; "
                        f"не назначено: {missing}. Статус H2: {plan['status']}. "
                        +(f"Причины: {reason_summary}. Подробности по галсам — во вкладке «Маршруты». " if missing else "")
                        +"Это не доказательство невозможности решения; сертификат безопасности не выдан."),
        })
    measured = recompute_metrics(input_dir, result)
    result["metrics"] = measured.get("metrics", {})
    return result


def main() -> None:
    import os
    import resource
    if os.environ.get("GMP_WORKER_MEMORY_MB"):
        limit = int(os.environ["GMP_WORKER_MEMORY_MB"]) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    import sys
    request_path, output_path = map(Path, sys.argv[1:3])
    req = json.loads(request_path.read_text())
    if req.get("action") == "h1":
        result = build_h1(Path(req["input_dir"]), Path(req["dataset"]), req["objective"], req.get("progress_file"))
    elif req.get("action") == "validate":
        from gmp.safety.h3_gate import validate_result
        candidate = json.loads(Path(req["candidate_path"]).read_text())
        result = validate_result(Path(req["input_dir"]), candidate, plan_id=req.get("plan_id"))
    else:
        result = build_live(Path(req["input_dir"]), Path(req["dataset"]), req["objective"], req["budget"], req["seed"], req.get("progress_file"), req.get("search_depth", "deep"))
    output_path.write_text(json.dumps(result, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
