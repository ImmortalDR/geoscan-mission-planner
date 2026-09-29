"""M12 FeasibilityMatrix — single source of truth (audit P0)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import MIN_SAFE_AGL_M
from .models import AtomicTask, Scene, Uav


def humanize_reason(code: str, *, uav_id: str = "", model: str = "") -> str:
    """W-04: human-readable reject for demo / jury."""
    who = model or uav_id or "борт"
    if code == "payload_class_mismatch":
        return f"{who}: нагрузка задачи несовместима с полезной нагрузкой борта"
    if code.startswith("wind_") and "exceeds" in code:
        return f"{who}: ветер слишком сильный для этого борта ({code})"
    if code.startswith("agl_"):
        return f"{who}: высота AGL ниже минимума безопасности {MIN_SAFE_AGL_M} м"
    if code == "fixed_wing_turn_unsafe_near_nfz":
        return f"{who}: разворот самолёта небезопасен у NFZ/границы"
    if code == "task_exceeds_usable_endurance":
        return f"{who}: длина задачи больше запаса энергии (грубая оценка H1)"
    return f"{who}: {code}"


@dataclass
class FeasibilityResult:
    eligible_uav_ids_by_task: dict[str, list[str]] = field(default_factory=dict)
    ineligible_reasons: dict[str, str] = field(default_factory=dict)
    ineligible_reasons_human: dict[str, str] = field(default_factory=dict)

    def as_contract(self) -> dict:
        return {
            "eligible_uav_ids_by_task": self.eligible_uav_ids_by_task,
            "ineligible_reasons": self.ineligible_reasons,
            # Additive demo field — H2 may ignore
            "ineligible_reasons_human": self.ineligible_reasons_human,
        }


def uav_eligible_for_task(uav: Uav, task: AtomicTask, wind_speed_ms: float) -> str | None:
    """Return ineligibility reason or None if OK."""
    if task.payload_class not in uav.payload_classes:
        return "payload_class_mismatch"
    if wind_speed_ms > uav.max_wind_ms:
        return f"wind_{wind_speed_ms}ms_exceeds_{uav.max_wind_ms}ms"
    if task.agl_m < MIN_SAFE_AGL_M:
        return f"agl_{task.agl_m}m_below_min_{MIN_SAFE_AGL_M}m"
    if not task.fixed_wing_safe and uav.is_fixed_wing:
        return "fixed_wing_turn_unsafe_near_nfz"
    if uav.ground_speed_ms > 0:
        need_s = task.survey_length_m / uav.ground_speed_ms
        if need_s > uav.usable_endurance_s:
            return "task_exceeds_usable_endurance"
    return None


def eligible_uavs_for_payload(scene: Scene, payload_class: str, *, require_fw_safe: bool = False) -> list[Uav]:
    wind = scene.mission.wind.speed_ms
    out = []
    for u in scene.fleet:
        if payload_class not in u.payload_classes:
            continue
        if wind > u.max_wind_ms:
            continue
        out.append(u)
    return out


def build_feasibility(scene: Scene, tasks: dict[str, AtomicTask] | list[AtomicTask]) -> FeasibilityResult:
    wind = scene.mission.wind.speed_ms
    seq = tasks.values() if isinstance(tasks, dict) else tasks
    result = FeasibilityResult()
    for task in seq:
        ok: list[str] = []
        for u in scene.fleet:
            reason = uav_eligible_for_task(u, task, wind)
            if reason:
                key = f"{task.id}:{u.id}"
                result.ineligible_reasons[key] = reason
                result.ineligible_reasons_human[key] = humanize_reason(
                    reason, uav_id=u.id, model=u.model
                )
            else:
                ok.append(u.id)
        result.eligible_uav_ids_by_task[task.id] = ok
    return result
