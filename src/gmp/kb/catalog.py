"""Versioned UAV Knowledge Base (TS §6).

The KB separates:

* ``uav_model``            - vendor model identity;
* ``uav_model_revision``   - catalog snapshot (schema + retrieved_at);
* ``operational_profile``  - planning limits after derating/reserve;
* ``physical_uav_instance``- the concrete tail number from ``fleet.json``.

Every technical fact carries provenance, and public passport maxima are never
used directly as planning limits.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

CATALOG_PATH = Path(__file__).with_name("uav_catalog_seed_v2.yaml")

#: Operational derating applied to public passport endurance/speed per class.
DERATING = {
    "fixed_wing": {"endurance": 0.80, "speed": 0.95, "wind": 0.90},
    "multirotor": {"endurance": 0.80, "speed": 0.85, "wind": 0.90},
    "vtol_fixed_wing": {"endurance": 0.80, "speed": 0.90, "wind": 0.90},
    "vtol": {"endurance": 0.80, "speed": 0.90, "wind": 0.90},
}

#: Turnaround / ground handling defaults (seconds) per vehicle class.
GROUND_TIMES = {
    "fixed_wing": {"takeoff_s": 180.0, "landing_s": 240.0, "service_s": 600.0},
    "multirotor": {"takeoff_s": 60.0, "landing_s": 60.0, "service_s": 300.0},
    "vtol_fixed_wing": {"takeoff_s": 120.0, "landing_s": 120.0, "service_s": 420.0},
    "vtol": {"takeoff_s": 120.0, "landing_s": 120.0, "service_s": 420.0},
}


@dataclass
class ProvenanceFact:
    """A single technical fact with its source chain."""

    key: str
    value: Any
    unit: str
    source: str
    source_url: str | None = None
    source_date: str | None = None
    retrieved_at: str | None = None
    priority: str = "official_product"
    confidence: float = 0.9
    notes: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "unit": self.unit,
            "source": self.source,
            "source_url": self.source_url,
            "source_date": self.source_date,
            "retrieved_at": self.retrieved_at,
            "priority": self.priority,
            "confidence": self.confidence,
            "notes": self.notes,
        }


@dataclass
class OperationalProfile:
    """Planning-safe limits derived from a model revision."""

    model_id: str
    vehicle_class: str
    operational_endurance_min: float
    planning_speed_ms: float
    max_wind_ms: float
    payload_classes: list[str]
    min_safe_agl_m: float
    max_altitude_msl_m: float | None
    takeoff_s: float
    landing_s: float
    service_s: float
    energy_reserve_fraction: float
    horizontal_separation_m: float
    vertical_separation_m: float
    turnaround_buffer_m: float
    derating: dict[str, float] = field(default_factory=dict)
    provenance: list[ProvenanceFact] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = {
            "model_id": self.model_id,
            "vehicle_class": self.vehicle_class,
            "operational_endurance_min": round(self.operational_endurance_min, 2),
            "planning_speed_ms": round(self.planning_speed_ms, 2),
            "max_wind_ms": round(self.max_wind_ms, 2),
            "payload_classes": list(self.payload_classes),
            "min_safe_agl_m": self.min_safe_agl_m,
            "max_altitude_msl_m": self.max_altitude_msl_m,
            "ground_times_s": {
                "takeoff": self.takeoff_s,
                "landing": self.landing_s,
                "service": self.service_s,
            },
            "energy_reserve_fraction": self.energy_reserve_fraction,
            "separation_m": {
                "horizontal": self.horizontal_separation_m,
                "vertical": self.vertical_separation_m,
            },
            "turnaround_buffer_m": self.turnaround_buffer_m,
            "derating": self.derating,
            "conflicts": self.conflicts,
            "provenance": [p.as_dict() for p in self.provenance],
        }
        return d


@dataclass
class UavModelRevision:
    model_id: str
    manufacturer: str
    model_name: str
    catalog_role: str
    vehicle_class: str
    schema_version: int
    retrieved_at: str
    specs: dict[str, Any]
    payload_classes: list[str]
    sources: list[dict[str, Any]]

    @property
    def revision_id(self) -> str:
        return f"{self.model_id}@v{self.schema_version}:{self.retrieved_at}"


def _first(specs: dict[str, Any], keys: Iterable[str]) -> tuple[str | None, Any]:
    for k in keys:
        if k in specs and specs[k] is not None:
            return k, specs[k]
    return None, None


class UavKnowledgeBase:
    """Loads the catalog and builds operational profiles."""

    #: separation defaults by class (metres); fixed wing needs a wider bubble
    SEPARATION = {
        "fixed_wing": (300.0, 50.0, 300.0),
        "multirotor": (50.0, 50.0, 30.0),
        "vtol_fixed_wing": (200.0, 50.0, 150.0),
        "vtol": (200.0, 50.0, 150.0),
    }

    def __init__(self, path: str | Path = CATALOG_PATH):
        self.path = Path(path)
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        self.raw = raw
        self.schema_version = int(raw.get("schema_version", 1))
        self.retrieved_at = str(raw.get("retrieved_at", ""))
        self.policy = raw.get("policy", {})
        self.default_reserve = float(self.policy.get("default_energy_reserve_fraction", 0.20))
        self.models: dict[str, UavModelRevision] = {}
        for m in raw.get("models", []):
            rev = UavModelRevision(
                model_id=m["id"],
                manufacturer=m.get("manufacturer", "unknown"),
                model_name=m.get("model", m["id"]),
                catalog_role=m.get("catalog_role", "reference_test_profile"),
                vehicle_class=m.get("vehicle_class", "multirotor"),
                schema_version=self.schema_version,
                retrieved_at=self.retrieved_at,
                specs=dict(m.get("specs", {})),
                payload_classes=list(m.get("payload_classes", [])),
                sources=list(m.get("sources", [])),
            )
            self.models[rev.model_id] = rev
        self._profiles: dict[str, OperationalProfile] = {}

    # ------------------------------------------------------------------ #
    def model_ids(self) -> list[str]:
        return list(self.models)

    def __len__(self) -> int:
        return len(self.models)

    def _primary_source(self, rev: UavModelRevision) -> dict[str, Any]:
        if rev.sources:
            return rev.sources[0]
        return {"title": "catalog seed", "url": None, "priority": "internal"}

    def profile(self, model_id: str) -> OperationalProfile:
        """Build (and cache) the operational planning profile for a model."""
        if model_id in self._profiles:
            return self._profiles[model_id]
        rev = self.models.get(model_id)
        if rev is None:
            raise KeyError(f"unknown UAV model: {model_id}")
        specs = rev.specs
        vclass = rev.vehicle_class
        derate = DERATING.get(vclass, DERATING["multirotor"])
        ground = GROUND_TIMES.get(vclass, GROUND_TIMES["multirotor"])
        src = self._primary_source(rev)
        prov: list[ProvenanceFact] = []
        conflicts: list[str] = []

        def fact(key, value, unit, notes=None, confidence=0.9):
            prov.append(
                ProvenanceFact(
                    key=key,
                    value=value,
                    unit=unit,
                    source=src.get("title", "catalog"),
                    source_url=src.get("url"),
                    source_date=rev.retrieved_at,
                    retrieved_at=rev.retrieved_at,
                    priority=src.get("priority", "official_product"),
                    confidence=confidence,
                    notes=notes,
                )
            )

        # --- endurance ------------------------------------------------- #
        key, passport_min = _first(
            specs,
            (
                "max_flight_time_min",
                "max_flight_time_min_product",
                "max_flight_time_min_manual_revision",
            ),
        )
        if passport_min is None:
            passport_min = 40.0
            conflicts.append("endurance not published; conservative default 40 min used")
        fact("passport_max_flight_time_min", passport_min, "min", "public maximum, not a planning limit")
        if (
            "max_flight_time_min_product" in specs
            and "max_flight_time_min_manual_revision" in specs
            and specs["max_flight_time_min_product"] != specs["max_flight_time_min_manual_revision"]
        ):
            conflicts.append(
                "endurance conflict product={}min manual={}min (retained, lower used)".format(
                    specs["max_flight_time_min_product"], specs["max_flight_time_min_manual_revision"]
                )
            )
            passport_min = min(
                specs["max_flight_time_min_product"], specs["max_flight_time_min_manual_revision"]
            )
        oper_endurance = float(passport_min) * derate["endurance"]
        fact(
            "operational_endurance_min",
            round(oper_endurance, 1),
            "min",
            f"derated by factor {derate['endurance']} from public maximum",
        )

        # --- speed ----------------------------------------------------- #
        speed_ms = None
        for k in ("cruise_speed_ms", "optimal_cruise_speed_ms", "max_horizontal_speed_ms", "cruise_speed_min_ms"):
            if k in specs:
                speed_ms = float(specs[k])
                fact(f"passport_{k}", specs[k], "m/s")
                break
        if speed_ms is None:
            for k in ("cruise_speed_kmh", "max_horizontal_speed_kmh", "max_speed_kmh", "speed_min_kmh"):
                if k in specs:
                    speed_ms = float(specs[k]) / 3.6
                    fact(f"passport_{k}", specs[k], "km/h")
                    break
        if speed_ms is None:
            speed_ms = 12.0
            conflicts.append("speed not published; conservative default 12 m/s used")
        planning_speed = speed_ms * derate["speed"]
        fact(
            "planning_speed_ms",
            round(planning_speed, 2),
            "m/s",
            f"derated by factor {derate['speed']}",
        )

        # --- wind ------------------------------------------------------ #
        wind_candidates = [
            float(specs[k])
            for k in (
                "max_wind_ms",
                "max_wind_ms_operational_default",
                "max_wind_ms_reference",
                "max_sustained_wind_ms",
                "max_wind_cruise_ms",
                "max_wind_hover_ms",
                "max_wind_flight_ms",
                "max_wind_takeoff_landing_ms",
            )
            if k in specs
        ]
        if "source_conflict_max_wind_ms" in specs:
            vals = specs["source_conflict_max_wind_ms"]
            conflicts.append(f"max wind conflict between sources {vals}; lower value used")
            wind_candidates.extend(float(v) for v in vals)
        passport_wind = min(wind_candidates) if wind_candidates else 10.0
        fact("passport_max_wind_ms", passport_wind, "m/s", "lowest published limit retained")
        oper_wind = passport_wind * derate["wind"]
        fact("operational_max_wind_ms", round(oper_wind, 2), "m/s", f"safety margin {1 - derate['wind']:.0%}")

        # --- altitude -------------------------------------------------- #
        min_agl = float(specs.get("min_safe_altitude_m", 50.0))
        max_msl = None
        for k in (
            "max_altitude_msl_m",
            "max_takeoff_altitude_msl_m",
            "service_ceiling_m",
            "max_flight_altitude_m",
        ):
            if k in specs:
                max_msl = float(specs[k])
                break
        fact("min_safe_agl_m", min_agl, "m")
        if max_msl is not None:
            fact("max_altitude_msl_m", max_msl, "m")

        hsep, vsep, turn_buf = self.SEPARATION.get(vclass, self.SEPARATION["multirotor"])
        prof = OperationalProfile(
            model_id=model_id,
            vehicle_class=vclass,
            operational_endurance_min=oper_endurance,
            planning_speed_ms=planning_speed,
            max_wind_ms=oper_wind,
            payload_classes=list(rev.payload_classes),
            min_safe_agl_m=min_agl,
            max_altitude_msl_m=max_msl,
            takeoff_s=ground["takeoff_s"],
            landing_s=ground["landing_s"],
            service_s=ground["service_s"],
            energy_reserve_fraction=self.default_reserve,
            horizontal_separation_m=hsep,
            vertical_separation_m=vsep,
            turnaround_buffer_m=turn_buf,
            derating=dict(derate),
            provenance=prov,
            conflicts=conflicts,
        )
        self._profiles[model_id] = prof
        return prof

    # ------------------------------------------------------------------ #
    def resolve_instance(self, fleet_entry: dict[str, Any]) -> tuple[OperationalProfile, list[str]]:
        """Merge a customer fleet entry with the KB profile.

        Customer-provided values override the catalog (TS §6 priority rules);
        every override is reported so the report can show the audit trail.
        """
        model_id = fleet_entry.get("model")
        notes: list[str] = []
        if model_id in self.models:
            prof = copy.deepcopy(self.profile(model_id))
        else:
            notes.append(f"model {model_id!r} is not in KB; instance data used as-is")
            prof = OperationalProfile(
                model_id=str(model_id),
                vehicle_class=fleet_entry.get("class", "multirotor"),
                operational_endurance_min=float(fleet_entry.get("operational_endurance_min", 40.0)),
                planning_speed_ms=float(fleet_entry.get("ground_speed_kmh", 40.0)) / 3.6,
                max_wind_ms=float(fleet_entry.get("max_wind_ms", 10.0)),
                payload_classes=list(fleet_entry.get("payload_classes", [])),
                min_safe_agl_m=50.0,
                max_altitude_msl_m=None,
                takeoff_s=GROUND_TIMES["multirotor"]["takeoff_s"],
                landing_s=GROUND_TIMES["multirotor"]["landing_s"],
                service_s=GROUND_TIMES["multirotor"]["service_s"],
                energy_reserve_fraction=self.default_reserve,
                horizontal_separation_m=100.0,
                vertical_separation_m=50.0,
                turnaround_buffer_m=50.0,
            )

        overrides = {
            "operational_endurance_min": "operational_endurance_min",
            "max_wind_ms": "max_wind_ms",
            "energy_reserve_fraction": "energy_reserve_fraction",
            "horizontal_separation_m": "horizontal_separation_m",
            "vertical_separation_m": "vertical_separation_m",
            "turnaround_buffer_m": "turnaround_buffer_m",
        }
        for src_key, attr in overrides.items():
            if src_key in fleet_entry and fleet_entry[src_key] is not None:
                old = getattr(prof, attr)
                new = float(fleet_entry[src_key])
                if abs(float(old) - new) > 1e-9:
                    notes.append(f"customer override {attr}: {round(float(old), 2)} -> {new}")
                setattr(prof, attr, new)
        if "ground_speed_kmh" in fleet_entry:
            new_speed = float(fleet_entry["ground_speed_kmh"]) / 3.6
            if abs(new_speed - prof.planning_speed_ms) > 1e-9:
                notes.append(
                    f"customer override planning_speed_ms: {prof.planning_speed_ms:.2f} -> {new_speed:.2f}"
                )
            prof.planning_speed_ms = new_speed
        if fleet_entry.get("payload_classes"):
            prof.payload_classes = list(fleet_entry["payload_classes"])
        if fleet_entry.get("class"):
            prof.vehicle_class = fleet_entry["class"]
        return prof, notes

    # ------------------------------------------------------------------ #
    def summary(self) -> dict[str, Any]:
        items = []
        for mid, rev in self.models.items():
            p = self.profile(mid)
            items.append(
                {
                    "model_id": mid,
                    "revision_id": rev.revision_id,
                    "manufacturer": rev.manufacturer,
                    "model": rev.model_name,
                    "catalog_role": rev.catalog_role,
                    "vehicle_class": rev.vehicle_class,
                    "operational_endurance_min": round(p.operational_endurance_min, 1),
                    "planning_speed_ms": round(p.planning_speed_ms, 2),
                    "max_wind_ms": round(p.max_wind_ms, 2),
                    "payload_classes": p.payload_classes,
                    "conflicts": p.conflicts,
                    "sources": rev.sources,
                }
            )
        return {
            "catalog": self.raw.get("catalog_name"),
            "schema_version": self.schema_version,
            "retrieved_at": self.retrieved_at,
            "model_count": len(items),
            "policy": self.policy,
            "models": items,
        }


_DEFAULT_KB: UavKnowledgeBase | None = None


def default_kb() -> UavKnowledgeBase:
    global _DEFAULT_KB
    if _DEFAULT_KB is None:
        _DEFAULT_KB = UavKnowledgeBase()
    return _DEFAULT_KB
