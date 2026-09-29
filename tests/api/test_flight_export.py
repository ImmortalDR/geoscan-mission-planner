import json
import xml.etree.ElementTree as ET

import pytest

from gmp.api.flight_export import geojson, kml


@pytest.fixture
def result():
    sorties = []
    for uid, ordinal in [("U&1", 1), ("U&1", 2), ("U<2", 1)]:
        points = [{"lon": 37.1234567890123 + index * .001, "lat": 55.70000000001,
                   "amsl_m": 185.123456789, "agl_m": 42.987654321,
                   "t": f"2026-06-15T09:0{ordinal}:{index}0+03:00", "speed_ms": 7.23456789,
                   "phase": phase, "job_id": "Job<&1" if phase == "survey" else None,
                   "remaining_endurance_s": 1500.25 - index * 10}
                  for index, phase in enumerate(["takeoff", "transit", "survey", "landing"])]
        sorties.append({"id": f"{uid}_S{ordinal}", "uav_id": uid, "index": ordinal,
                        "start_site": "A<&", "landing_site": "B", "task_ids": ["Job<&1#G0"],
                        "t_start": points[0]["t"], "t_end": points[-1]["t"], "waypoints": points})
    return {"scene_id": "test<&", "sorties": sorties}


@pytest.mark.parametrize("uid", [None, "U&1", "U<2"])
def test_exports_preserve_every_waypoint_and_aircraft_selection(result, uid):
    expected = [s for s in result["sorties"] if uid is None or s["uav_id"] == uid]
    exported = geojson(result, {"input_sha256": "test"}, uid)
    # JSON serialization must also retain floating-point precision and nulls.
    exported = json.loads(json.dumps(exported))
    lines = [f for f in exported["features"] if f["geometry"]["type"] == "LineString"]
    assert [f["properties"]["id"] for f in lines] == [s["id"] for s in expected]
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    document = ET.fromstring(kml(result, {"test": "<>&"}, uid))
    marks = document.findall(".//k:Placemark", ns)
    xml_points = [p for p in marks if p.find("k:Point", ns) is not None]
    json_points = [f for f in exported["features"] if f["geometry"]["type"] == "Point"]
    expected_points = [(s, i, w) for s in expected for i, w in enumerate(s["waypoints"])]
    assert len(xml_points) == len(json_points) == len(expected_points)
    for xml_point, json_point, (sortie, index, point) in zip(xml_points, json_points, expected_points):
        values = {v.attrib["name"]: v.find("k:value", ns).text
                  for v in xml_point.findall("k:ExtendedData/k:Data", ns)}
        assert int(values["waypoint_index"]) == index
        assert values["uav_id"] == sortie["uav_id"]
        assert values["start_site"] == sortie["start_site"]
        for key, value in point.items():
            assert json_point["properties"][key] == value
            assert (values[key] if isinstance(value, str) else json.loads(values[key])) == value
        coords = [float(v) for v in xml_point.find("k:Point/k:coordinates", ns).text.split(",")]
        assert coords == json_point["geometry"]["coordinates"] == [point["lon"], point["lat"], point["amsl_m"]]
    spans = document.findall(".//k:TimeSpan", ns)
    assert [s.find("k:begin", ns).text for s in spans] == [s["t_start"] for s in expected]


def test_unknown_uav_rejected(result):
    for export in [geojson, kml]:
        with pytest.raises(ValueError, match="No flights"):
            export(result, {}, "unknown")
