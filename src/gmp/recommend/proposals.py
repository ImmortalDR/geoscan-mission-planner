"""Bounded input alternatives, never safety verdicts or automatic mutations.

Every returned document set needs a fresh H1/H2 calculation and independent H3
validation. Only the orchestration layer may offer a verified alternative.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

from shapely.geometry import shape
from shapely.ops import unary_union


def catalog_snapshot() -> list[dict]:
    from gmp.kb.catalog import default_kb

    kb = default_kb()
    result = []
    for name, lookup in (("geoscan_201", "geoscan_201"), ("geoscan_401", "geoscan_401_geo"),
                         ("geoscan_701", "geoscan_701"), ("geoscan_801", "geoscan_801"),
                         ("gemini", "geoscan_gemini")):
        p = kb.profile(lookup)
        result.append({
            "model": name, "class": p.vehicle_class, "ground_speed_kmh": p.planning_speed_ms * 3.6,
            "operational_endurance_min": p.operational_endurance_min, "max_wind_ms": p.max_wind_ms,
            "payload_classes": list(p.payload_classes), "energy_reserve_fraction": p.energy_reserve_fraction,
            "horizontal_separation_m": p.horizontal_separation_m, "vertical_separation_m": p.vertical_separation_m,
            "turnaround_buffer_m": p.turnaround_buffer_m, "takeoff_time_s": p.takeoff_s,
            "landing_time_s": p.landing_s, "service_time_s": p.service_s,
            "catalog_provenance": p.as_dict(),
            "parameter_status": "catalog_derived_planning_assumption_not_flight_authorization",
        })
    return result


def build_proposals(snapshot: dict, catalog: list[dict], limit: int = 8) -> list[dict]:
    """Return deterministic changed JSON documents from explicit source inputs."""
    limit = max(0, min(int(limit), 8))
    if not limit:
        return []
    source = deepcopy(snapshot)
    mission, fleet = source["mission"], source["fleet"]["uavs"]
    layers = source["layers"]
    sites = layers["landing_sites"]["features"]
    allowed = unary_union([shape(f["geometry"]) for f in layers["allowed_airspace"]["features"]])
    forbidden = unary_union([shape(f["geometry"]) for f in layers["no_fly_zones"]["features"]])
    usable_sites = [f for f in sites if f["properties"]["role"] != "reserve"
                    and allowed.covers(shape(f["geometry"])) and not forbidden.intersects(shape(f["geometry"]))]
    usable_sites.sort(key=lambda f: (not f["properties"].get("candidate", False), f["properties"]["id"]))
    groups: dict[str, list] = {kind: [] for kind in ("site", "fleet", "time")}
    fingerprints = set()

    def append(group, kind, title, documents, changes, assumptions=None):
        if not documents:
            return
        encoded = json.dumps(documents, sort_keys=True, separators=(",", ":"), allow_nan=False)
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        if fingerprint in fingerprints:
            return
        fingerprints.add(fingerprint)
        notes = assumptions or []
        metadata = deepcopy(source.get("metadata", {}))
        metadata["recommendation_assumptions"] = notes
        metadata["recommendation_changes"] = changes
        documents["metadata.json"] = metadata
        groups[group].append({"id": "proposal_" + fingerprint[:12], "type": kind, "title": title,
                              "changes": changes, "documents": documents, "assumptions": notes,
                              "verified": False, "requires_replan": True, "requires_user_selection": True})

    def site_variant(start=None, landing=None):
        changed_sites, changed_fleet = deepcopy(layers["landing_sites"]), deepcopy(source["fleet"])
        chosen = {x for x in (start, landing) if x}
        renamed = {}
        for feature in changed_sites["features"]:
            p = feature["properties"]
            if p["id"] in chosen and (p.get("candidate") or p["id"].upper().startswith("CANDIDATE")):
                original = p["id"]
                active = "ACTIVE_" + hashlib.sha256(original.encode()).hexdigest()[:12]
                existing = {f["properties"]["id"] for f in changed_sites["features"]}
                while active in existing:
                    active += "_A"
                renamed[original] = active
                p.update(id=active, candidate=False)
        changed = False
        for uav in changed_fleet["uavs"]:
            for key, desired in (("start_site", start), ("landing_site", landing)):
                value = renamed.get(desired, desired) if desired else renamed.get(uav[key], uav[key])
                changed |= uav[key] != value
                uav[key] = value
        if not changed:
            return
        changed_mission = deepcopy(mission)
        if any(u["start_site"] != u["landing_site"] for u in changed_fleet["uavs"]):
            changed_mission["allow_different_start_end"] = True
        title = (f"Старт и посадка на площадке {start}" if start == landing else
                 f"Старт: {start or 'исходный'}; посадка: {landing or 'исходная'}")
        kind = "change_base" if start == landing else "different_start_end" if start and landing else "change_start_site" if start else "change_landing_site"
        docs = {"landing_sites.geojson": changed_sites, "fleet.json": changed_fleet}
        changes = [{"field": "fleet." + key, "value": renamed.get(value, value), "source_site": value}
                   for key, value in (("start_site", start), ("landing_site", landing)) if value]
        if renamed:
            changes.append({"field": "activated_sites", "value": renamed})
        if changed_mission != mission:
            docs["mission.json"] = changed_mission
            changes.append({"field": "mission.allow_different_start_end", "value": True})
        append("site", kind, title, docs, changes,
               ["Площадка должна быть доступна для эксплуатации; это не разрешение на полёт"])

    starts = [f["properties"]["id"] for f in usable_sites if f["properties"]["role"] in {"start", "both"}]
    landings = [f["properties"]["id"] for f in usable_sites if f["properties"]["role"] in {"landing", "both"}]
    # S06's explicit candidate base is attempted before equipment/time changes.
    for feature in usable_sites[:8]:
        p = feature["properties"]
        if p["role"] == "both":
            site_variant(p["id"], p["id"])
    for name in starts[:3]:
        site_variant(start=name)
    for name in landings[:3]:
        site_variant(landing=name)
    for start in starts[:2]:
        for landing in landings[:2]:
            if start != landing:
                site_variant(start, landing)

    required = {f["properties"]["survey_type"] for f in layers["survey_areas"]["features"]}
    wind = mission["wind"]["speed_ms"]
    eligible_catalog = [deepcopy(u) for u in catalog if u["max_wind_ms"] >= wind and required.intersection(u["payload_classes"])]
    covered = set().union(*(set(u["payload_classes"]) for u in fleet if u["max_wind_ms"] >= wind))
    missing = required - covered
    eligible_catalog.sort(key=lambda u: (-len(missing.intersection(u["payload_classes"])),
                                        -len(required.intersection(u["payload_classes"])),
                                        -u["operational_endurance_min"], u["model"]))

    def equipment(template, original, identifier):
        row = deepcopy(template)
        row.update(id=identifier, start_site=original["start_site"], landing_site=original["landing_site"])
        for field in ("energy_reserve_fraction", "horizontal_separation_m", "vertical_separation_m", "turnaround_buffer_m"):
            row[field] = max(original.get(field, 0), row.get(field, 0))
        return row

    for original in sorted(fleet, key=lambda u: u["id"]):
        for template in eligible_catalog[:3]:
            if template["model"] == original["model"]:
                continue
            original_classes = set(original["payload_classes"])
            if not (missing.intersection(template["payload_classes"]) or original["max_wind_ms"] < wind or
                    template["operational_endurance_min"] > original["operational_endurance_min"]):
                continue
            # A replacement must not remove a survey class already available on
            # this aircraft; otherwise it could introduce an unrelated deficit.
            if not (required & original_classes).issubset(template["payload_classes"]):
                continue
            changed = deepcopy(source["fleet"])
            changed["uavs"] = [equipment(template, u, u["id"]) if u["id"] == original["id"] else u for u in changed["uavs"]]
            append("fleet", "replace_uav", f"При наличии {template['model']}: заменить БВС {original['id']}",
                   {"fleet.json": changed}, [{"field": f"fleet.{original['id']}.model", "value": template["model"]}],
                   ["Наличие другого БВС не подтверждено", "Параметры взяты из снимка каталога, резерв не уменьшен"])
            break

    if len(fleet) < 10 and (fleet or starts and landings):
        original = fleet[0] if fleet else {"start_site": starts[0], "landing_site": landings[0], "energy_reserve_fraction": 0.2}
        templates = eligible_catalog if missing else [*sorted(fleet, key=lambda u: u["id"]), *eligible_catalog]
        for template in templates[:3]:
            if template["max_wind_ms"] < wind:
                continue
            changed = deepcopy(source["fleet"])
            ids = {u["id"] for u in fleet}
            new_id = "EXTRA_01"
            while new_id in ids:
                new_id += "_A"
            changed["uavs"].append(equipment(template, original, new_id))
            append("fleet", "add_uav", f"При наличии дополнительного {template['model']}: добавить БВС",
                   {"fleet.json": changed}, [{"field": "fleet.add", "value": new_id, "model": template["model"]}],
                   ["Наличие дополнительного БВС не подтверждено", "Зарезервированный ресурс и ограничения не ослабляются"])
            break

    def instant(value):
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError("Recommendation windows require timezone")
        return date

    start = instant(mission["mission_window"]["start"])
    end = instant(mission["mission_window"]["end"])
    if mission.get("daylight_window"):
        start = max(start, instant(mission["daylight_window"]["start"]))
        end = min(end, instant(mission["daylight_window"]["end"]))
    delayed = sorted({instant(f["properties"]["active_to"]) + timedelta(seconds=1)
                      for f in layers["temporal_airspace"]["features"] if f["properties"].get("active_to")})
    delayed = [value for value in delayed if start < value < end]
    for value in delayed[:3]:
        changed = deepcopy(mission)
        changed["mission_window"]["start"] = value.isoformat()
        append("time", "delay_start", f"Начать после окончания временного запрета: {value.isoformat()}",
               {"mission.json": changed}, [{"field": "mission.mission_window.start", "value": value.isoformat()}],
               ["Конец исходного окна и светового дня не расширяется; погода не подменяется"])

    selected = []
    while len(selected) < limit and any(groups.values()):
        for group in groups.values():
            if group and len(selected) < limit:
                selected.append(group.pop(0))
    return selected


def generate_proposals(input_dir: Path, limit: int = 3) -> list[dict]:
    """API adapter; reads input documents only, never reference answers."""
    directory = Path(input_dir)
    def read(name):
        return json.loads((directory / name).read_text(encoding="utf-8"))

    layers = ("survey_areas", "allowed_airspace", "no_fly_zones", "temporal_airspace", "landing_sites", "obstacles")
    snapshot = {"layers": {name: read(name + ".geojson") for name in layers},
                **{name: read(name + ".json") for name in ("mission", "fleet", "payload_catalog", "metadata")}}
    return build_proposals(snapshot, catalog_snapshot(), limit)
