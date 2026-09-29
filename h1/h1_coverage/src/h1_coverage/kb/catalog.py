"""M4 UAVKnowledgeBase — YAML seed with provenance."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..models import CameraModel, Uav

SEED_PATH = Path(__file__).with_name("seed.yaml")

MVP_MODELS = (
    "geoscan_201",
    "geoscan_401",
    "geoscan_701",
    "geoscan_801",
    "geoscan_gemini",
)


class KnowledgeBase:
    """Contract: h1.m4.uav_kb.v1"""

    def __init__(self, path: Path | None = None):
        raw = yaml.safe_load((path or SEED_PATH).read_text(encoding="utf-8"))
        self.raw = raw
        self.defaults = raw.get("defaults") or {}
        self.models: dict[str, dict[str, Any]] = {m["id"]: m for m in raw.get("models") or []}
        self.cameras: dict[str, CameraModel] = {
            cid: CameraModel(**{k: v for k, v in c.items() if k in CameraModel.__dataclass_fields__})
            for cid, c in (raw.get("cameras") or {}).items()
        }

    def model_ids(self) -> list[str]:
        return sorted(self.models)

    def provenance_for(self, model_id: str) -> dict[str, Any]:
        prof = self.models.get(model_id) or {}
        prov = dict(self.defaults.get("provenance") or {})
        prov.update(prof.get("provenance") or {})
        return prov

    def resolve_uav(self, entry: dict[str, Any]) -> Uav:
        mid = entry.get("model", "")
        prof = self.models.get(mid)
        if prof is None:
            for m in self.models.values():
                if mid in (m.get("aliases") or []) or mid == m.get("id"):
                    prof = m
                    break
        if prof is None:
            speed = entry.get("ground_speed_ms")
            if speed is None and entry.get("ground_speed_kmh") is not None:
                speed = float(entry["ground_speed_kmh"]) / 3.6
            return Uav(
                id=entry["id"],
                model=mid or "unknown",
                uav_class=entry.get("class", entry.get("uav_class", "multirotor")),
                ground_speed_ms=float(speed or 10.0),
                operational_endurance_min=float(entry.get("operational_endurance_min", 30)),
                max_wind_ms=float(entry.get("max_wind_ms", 10)),
                payload_classes=list(entry.get("payload_classes") or ["rgb"]),
                energy_reserve_fraction=float(entry.get("energy_reserve_fraction", 0.2)),
                horizontal_separation_m=float(entry.get("horizontal_separation_m", 50)),
                vertical_separation_m=float(entry.get("vertical_separation_m", 30)),
                turnaround_buffer_m=float(entry.get("turnaround_buffer_m", 50)),
                start_site=entry.get("start_site"),
                landing_site=entry.get("landing_site"),
                cruise_agl_m=float(entry.get("cruise_agl_m", 120)),
            )
        speed = entry.get("ground_speed_ms", prof.get("ground_speed_ms"))
        if speed is None and entry.get("ground_speed_kmh") is not None:
            speed = float(entry["ground_speed_kmh"]) / 3.6
        return Uav(
            id=entry["id"],
            model=prof["id"],
            uav_class=entry.get("class", prof.get("class", "multirotor")),
            ground_speed_ms=float(speed),
            operational_endurance_min=float(
                entry.get("operational_endurance_min", prof["operational_endurance_min"])
            ),
            max_wind_ms=float(entry.get("max_wind_ms", prof["max_wind_ms"])),
            payload_classes=list(entry.get("payload_classes") or prof["payload_classes"]),
            energy_reserve_fraction=float(entry.get("energy_reserve_fraction", 0.2)),
            horizontal_separation_m=float(entry.get("horizontal_separation_m", 50)),
            vertical_separation_m=float(entry.get("vertical_separation_m", 30)),
            turnaround_buffer_m=float(
                entry.get("turnaround_buffer_m", prof.get("turnaround_buffer_m", 50))
            ),
            start_site=entry.get("start_site"),
            landing_site=entry.get("landing_site"),
            cruise_agl_m=float(entry.get("cruise_agl_m", prof.get("cruise_agl_m", 120))),
        )

    def camera_for(self, payload_type: str) -> CameraModel:
        if payload_type in self.cameras:
            return self.cameras[payload_type]
        return self.cameras.get("rgb_default") or CameraModel(
            "rgb_default", 6000, 4000, 16.0, 3.9
        )


_KB: KnowledgeBase | None = None


def default_kb() -> KnowledgeBase:
    global _KB
    if _KB is None:
        _KB = KnowledgeBase()
    return _KB
