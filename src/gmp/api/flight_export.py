"""Lossless waypoint exports of independently checked service trajectories.

KML ExtendedData values use JSON for non-string values. Segment attributes
(phase, job_id and speed_ms) belong to the segment ending at that waypoint.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET


SCHEMA = "geoscan.flight-export.v2"
UNITS = {"lon": "degrees east, WGS84", "lat": "degrees north, WGS84",
         "amsl_m": "metres AMSL, source DEM datum", "agl_m": "metres above source DEM",
         "speed_ms": "metres/second", "remaining_endurance_s": "seconds",
         "t": "ISO8601 with timezone", "waypoint_index": "zero-based"}


def select_sorties(result: dict, uav_id: str | None = None) -> list[dict]:
    sorties = [s for s in result["sorties"] if uav_id is None or s["uav_id"] == uav_id]
    if not sorties:
        raise ValueError("No flights for the selected UAV")
    return sorties


def sortie_properties(sortie: dict) -> dict:
    return {key: value for key, value in sortie.items() if key != "waypoints"}


def point_properties(sortie: dict, index: int, point: dict) -> dict:
    return {**point, "feature_type": "waypoint", "sortie_id": sortie["id"],
            "uav_id": sortie["uav_id"], "waypoint_index": index,
            "start_site": sortie["start_site"], "landing_site": sortie["landing_site"]}


def coordinates(point: dict) -> list:
    return [point["lon"], point["lat"], point["amsl_m"]]


def geojson(result: dict, context: dict, uav_id: str | None = None) -> dict:
    sorties = select_sorties(result, uav_id)
    features = []
    for sortie in sorties:
        features.append({"type": "Feature", "properties": {
            **sortie_properties(sortie), "feature_type": "sortie", "status": "SAFE"},
            "geometry": {"type": "LineString", "coordinates": [coordinates(w) for w in sortie["waypoints"]]}})
        for index, point in enumerate(sortie["waypoints"]):
            features.append({"type": "Feature", "properties": point_properties(sortie, index, point),
                             "geometry": {"type": "Point", "coordinates": coordinates(point)}})
    return {"type": "FeatureCollection", "schema": SCHEMA, "features": features,
            "units": UNITS, "segment_attributes": "incoming segment at waypoint_index > 0",
            "selected_uav_id": uav_id, "context": context}


def kml(result: dict, context: dict, uav_id: str | None = None) -> bytes:
    sorties = select_sorties(result, uav_id)
    root = ET.Element("kml", xmlns="http://www.opengis.net/kml/2.2")
    document = ET.SubElement(root, "Document")
    ET.SubElement(document, "name").text = f"Flight plan: {result['scene_id']}"
    ET.SubElement(document, "description").text = (
        "Verified model trajectory. Each UAV has a folder with its sorties and ordered waypoints. "
        "Waypoint phase, job_id and speed_ms describe the incoming segment. "
        "Heights retain the source DEM vertical datum; this is not an autopilot command file.")

    def data(parent, properties):
        extended = ET.SubElement(parent, "ExtendedData")
        for key, value in properties.items():
            item = ET.SubElement(extended, "Data", name=key)
            ET.SubElement(item, "value").text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, allow_nan=False)

    def folder(parent, name):
        node = ET.SubElement(parent, "Folder")
        ET.SubElement(node, "name").text = name
        return node

    def geometry(parent, kind, points):
        node = ET.SubElement(parent, kind)
        ET.SubElement(node, "altitudeMode").text = "absolute"
        ET.SubElement(node, "coordinates").text = " ".join(
            ",".join(str(value) for value in coordinates(point)) for point in points)

    data(document, {"schema": SCHEMA, "units": UNITS, "context": context,
                    "selected_uav_id": uav_id})
    for uid in dict.fromkeys(s["uav_id"] for s in sorties):
        aircraft = folder(document, uid)
        for sortie in (s for s in sorties if s["uav_id"] == uid):
            flight = folder(aircraft, sortie["id"])
            data(flight, sortie_properties(sortie))
            route = ET.SubElement(flight, "Placemark")
            ET.SubElement(route, "name").text = sortie["id"]
            span = ET.SubElement(route, "TimeSpan")
            ET.SubElement(span, "begin").text = sortie["t_start"]
            ET.SubElement(span, "end").text = sortie["t_end"]
            data(route, {**sortie_properties(sortie), "feature_type": "sortie", "status": "SAFE"})
            geometry(route, "LineString", sortie["waypoints"])
            points = folder(flight, "Waypoints (ordered)")
            for index, point in enumerate(sortie["waypoints"]):
                waypoint = ET.SubElement(points, "Placemark")
                ET.SubElement(waypoint, "name").text = f"{index:05d} · {point['phase']}"
                # Keep thousands of icons from obscuring the route by default.
                ET.SubElement(waypoint, "visibility").text = "0"
                stamp = ET.SubElement(waypoint, "TimeStamp")
                ET.SubElement(stamp, "when").text = point["t"]
                data(waypoint, point_properties(sortie, index, point))
                geometry(waypoint, "Point", [point])
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
