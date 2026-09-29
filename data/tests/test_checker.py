"""Small analytically understandable scenes exercise independent H3 checks."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import Point, box, mapping
from shapely.ops import transform

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from h3check import Segment, check_result, load_scene


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.epoch = datetime(2026, 6, 15, 9, tzinfo=timezone.utc)
        self.origin = np.array([500000.0, 6170000.0])
        self.to_wgs = Transformer.from_crs(32637, 4326, always_xy=True)
        self.write("metadata.json", {"scenario_id": "test", "metric_crs": "EPSG:32637", "temporal_altitude_reference": "AMSL"})
        self.mission = {"objectives": ["makespan", "total_flight"],
                        "mission_window": {"start": self.time(0), "end": self.time(7200)},
                        "wind": {"speed_ms": 3}, "service_time_min": 1,
                        "require_complete_coverage": True,
                        "validation_policy": {"coverage_tolerance_fraction": .001,
                                              "min_agl_m": 40, "altitude_tolerance_m": 3,
                                              "terrain_sample_step_m": 20,
                                              "takeoff_landing_corridor_radius_m": 100}}
        self.write("mission.json", self.mission)
        self.layer("survey_areas.geojson", [(box(-80, -20, 80, 20), {"id": "JOB", "survey_type": "rgb", "payload_profile": "RGB"})])
        self.layer("allowed_airspace.geojson", [(box(-1500, -1500, 1500, 1500), {"id": "ALLOWED"})])
        for name in ["no_fly_zones.geojson", "obstacles.geojson", "temporal_airspace.geojson"]:
            self.layer(name, [])
        self.sites = [(Point(-200, 0), {"id": "START", "role": "start", "candidate": False}),
                      (Point(200, 0), {"id": "END", "role": "landing", "candidate": False})]
        self.layer("landing_sites.geojson", self.sites)
        self.uav = {"id": "U1", "model": "gemini", "class": "multirotor", "ground_speed_kmh": 36,
                    "operational_endurance_min": 60, "max_wind_ms": 10,
                    "payload_classes": ["rgb"], "energy_reserve_fraction": .2,
                    "horizontal_separation_m": 20, "vertical_separation_m": 10,
                    "turnaround_buffer_m": 20, "start_site": "START", "landing_site": "END",
                    "takeoff_time_s": 10, "landing_time_s": 10, "service_time_s": 60}
        self.write("fleet.json", {"uavs": [self.uav]})
        self.payload = {"id": "RGB", "type": "rgb", "nominal_agl_m": 100, "gsd_cm": 2,
                        "front_overlap": .8, "side_overlap": .5, "survey_speed_factor": 1,
                        "camera": {"image_width_px": 6000, "image_height_px": 4000,
                                   "focal_length_mm": 20, "pixel_pitch_um": 4}}
        self.write("payload_catalog.json", {"payload_profiles": [self.payload]})
        self.dem_transform = from_origin(self.origin[0] - 2000, self.origin[1] + 2000, 10, 10)
        with rasterio.open(self.directory / "dem.tif", "w", driver="GTiff", height=400, width=400,
                           count=1, dtype="float32", crs="EPSG:32637", transform=self.dem_transform) as dataset:
            dataset.write(np.full((400, 400), 100, dtype="float32"), 1)
        self.result = {"schema": "geoscan.h3.result.v1", "scene_id": "test", "objective": "makespan",
                       "status": "SAFE", "optimality": {"status": "unknown"},
                       "metrics": {"distance_m": 400, "total_flight_s": 60, "makespan_s": 60, "coverage_percent": 100},
                       "sorties": [self.sortie()]}

    def time(self, seconds):
        return (self.epoch + timedelta(seconds=seconds)).isoformat()

    def write(self, name, value):
        (self.directory / name).write_text(json.dumps(value), encoding="utf-8")

    def layer(self, name, entries):
        def projected_to_wgs(x, y, z=None):
            return self.to_wgs.transform(np.asarray(x) + self.origin[0], np.asarray(y) + self.origin[1])
        self.write(name, {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": mapping(transform(projected_to_wgs, geom)), "properties": props}
            for geom, props in entries]})

    def sortie(self, uid="U1", rotation=False, offset=0):
        route = [(-200, 0, 0, 0, "takeoff", None, 0),
                 (-200, 0, 100, 10, "takeoff", None, 0),
                 (-100, 0, 100, 20, "transit", None, 10),
                 (100, 0, 100, 40, "survey", "JOB", 10),
                 (200, 0, 100, 50, "transit", None, 10),
                 (200, 0, 0, 60, "landing", None, 0)]
        points = []
        for x, y, agl, seconds, phase, job, speed in route:
            if rotation:
                x, y = -y, x
            lon, lat = self.to_wgs.transform(x + self.origin[0], y + self.origin[1])
            points.append({"lon": lon, "lat": lat, "agl_m": agl, "amsl_m": agl + 100,
                           "t": self.time(seconds + offset), "phase": phase, "job_id": job,
                           "speed_ms": speed, "remaining_endurance_s": 2880 - seconds})
        return {"id": "FLIGHT_" + uid, "uav_id": uid, "index": 0,
                "start_site": "START2" if rotation else "START", "landing_site": "END2" if rotation else "END",
                "t_start": self.time(offset), "t_end": self.time(offset + 60), "flight_time_s": 60,
                "distance_m": 400, "waypoints": points}

    def add_crossing_flight(self, offset=0):
        self.sites.extend([(Point(0, -200), {"id": "START2", "role": "start", "candidate": False}),
                           (Point(0, 200), {"id": "END2", "role": "landing", "candidate": False})])
        self.layer("landing_sites.geojson", self.sites)
        second = {**self.uav, "id": "U2", "start_site": "START2", "landing_site": "END2"}
        self.write("fleet.json", {"uavs": [self.uav, second]})
        self.result["sorties"].append(self.sortie("U2", rotation=True, offset=offset))
        self.result["metrics"].update(distance_m=800, total_flight_s=120, makespan_s=60 + offset)

    def check(self):
        return check_result(self.directory, self.result)

    def codes(self):
        report = self.check()
        return {v["code"] for v in report["violations"]}

    def test_valid_hand_computed_reference(self):
        report = self.check()
        self.assertTrue(report["passed"], report)
        self.assertAlmostEqual(report["metrics"]["distance_m"], 400, places=5)

    def test_no_fly_crossing_between_waypoints(self):
        self.layer("no_fly_zones.geojson", [(box(-10, -10, 10, 10), {"id": "NFZ"})])
        self.assertIn("NFZ_VIOLATION", self.codes())

    def test_active_time_and_altitude_crossing_between_waypoints(self):
        self.layer("temporal_airspace.geojson", [(box(-10, -10, 10, 10), {
            "id": "TIMED", "min_alt_m": 190, "max_alt_m": 210,
            "active_from": self.time(29), "active_to": self.time(31), "restriction_type": "prohibited"})])
        self.assertIn("TEMPORAL_AIRSPACE", self.codes())

    def test_same_zone_at_nonoverlapping_time_passes(self):
        self.layer("temporal_airspace.geojson", [(box(-10, -10, 10, 10), {
            "id": "TIMED", "min_alt_m": 190, "max_alt_m": 210,
            "active_from": self.time(50), "active_to": self.time(55), "restriction_type": "prohibited"})])
        report = self.check()
        self.assertTrue(report["passed"], report)

    def test_continuous_crossing_detected_without_waypoint_coincidence(self):
        self.add_crossing_flight()
        self.assertIn("AIRCRAFT_CONFLICT", self.codes())

    def test_crossing_at_different_times_is_safe(self):
        self.add_crossing_flight(offset=100)
        report = self.check()
        self.assertTrue(report["passed"], report)

    def test_missing_and_nonfinite_fields_fail_closed(self):
        original = deepcopy(self.result)
        for key in ["agl_m", "amsl_m", "t", "phase", "job_id", "remaining_endurance_s"]:
            with self.subTest(key=key):
                self.result = deepcopy(original)
                del self.result["sorties"][0]["waypoints"][2][key]
                self.assertIn("INVALID_DATA", self.codes())
        self.result = deepcopy(original)
        self.result["sorties"][0]["waypoints"][2]["lon"] = float("nan")
        self.assertIn("INVALID_DATA", self.codes())

    def test_naive_time_unknown_id_and_empty_safe_fail_closed(self):
        original = deepcopy(self.result)
        self.result["sorties"][0]["waypoints"][2]["t"] = "2026-06-15T09:00:20"
        self.assertIn("INVALID_DATA", self.codes())
        self.result = deepcopy(original)
        self.result["sorties"][0]["uav_id"] = "UNKNOWN"
        self.assertIn("INVALID_DATA", self.codes())
        self.result = deepcopy(original)
        self.result["sorties"] = []
        self.assertIn("EMPTY_SAFE", self.codes())

    def test_coverage_gap_and_false_metric_are_independent(self):
        self.layer("survey_areas.geojson", [(box(-80, 300, 80, 340), {"id": "JOB", "survey_type": "rgb", "payload_profile": "RGB"})])
        codes = self.codes()
        self.assertIn("COVERAGE_GAP", codes)
        self.assertIn("CLAIMED_METRIC_MISMATCH", codes)

    def test_resource_reserve_is_enforced(self):
        self.uav["operational_endurance_min"] = 1
        self.write("fleet.json", {"uavs": [self.uav]})
        self.assertIn("RESOURCE_EXCEEDED", self.codes())

    def test_distance_claim_is_recomputed(self):
        self.result["metrics"]["distance_m"] = 1
        self.assertIn("CLAIMED_METRIC_MISMATCH", self.codes())

    def test_wait_before_takeoff_counts_in_makespan(self):
        self.result["sorties"] = [self.sortie(offset=100)]
        self.assertIn("CLAIMED_METRIC_MISMATCH", self.codes())
        self.result["metrics"]["makespan_s"] = 160
        self.assertTrue(self.check()["passed"])

    def test_obstacle_is_checked_between_waypoints(self):
        self.layer("obstacles.geojson", [(box(-2, -2, 2, 2), {"id": "TOWER", "height_m": 110,
                                                                     "horizontal_buffer_m": 1, "vertical_buffer_m": 10})])
        self.assertIn("OBSTACLE_CLEARANCE", self.codes())

    def test_takeoff_cannot_disguise_low_transit(self):
        self.result["sorties"][0]["waypoints"][3].update(phase="takeoff", job_id=None)
        self.assertIn("PHASE_CORRIDOR", self.codes())
        self.assertIn("FLIGHT_PHASES", self.codes())

    def test_wrong_site_role_fails(self):
        self.sites[1][1]["role"] = "reserve"
        self.layer("landing_sites.geojson", self.sites)
        self.assertIn("SITE_ROLE", self.codes())

    def test_false_infeasibility_is_rejected(self):
        self.result.update(status="INFEASIBLE", sorties=[], metrics={},
                           diagnosis={"proof": {"type": "no_compatible_uav", "job_id": "JOB"}})
        self.assertIn("UNPROVEN_INFEASIBLE", self.codes())
        self.uav["payload_classes"] = ["thermal"]
        self.write("fleet.json", {"uavs": [self.uav]})
        self.assertTrue(self.check()["passed"])

    def test_far_point_does_not_prove_entire_job_unreachable(self):
        self.uav["operational_endurance_min"] = 1
        self.write("fleet.json", {"uavs": [self.uav]})
        self.layer("survey_areas.geojson", [(box(-80, -20, 10000, 20), {
            "id": "JOB", "survey_type": "rgb", "payload_profile": "RGB"})])
        lon, lat = self.to_wgs.transform(*(self.origin + [9000, 0]))
        self.result.update(status="INFEASIBLE", sorties=[], metrics={}, diagnosis={"proof": {
            "type": "unreachable_required_point", "job_id": "JOB", "point": [lon, lat]}})
        self.assertIn("UNPROVEN_INFEASIBLE", self.codes())

    def test_entire_far_job_supports_infeasibility_proof(self):
        self.uav["operational_endurance_min"] = 1
        self.write("fleet.json", {"uavs": [self.uav]})
        self.layer("survey_areas.geojson", [(box(9000, -20, 10000, 20), {
            "id": "JOB", "survey_type": "rgb", "payload_profile": "RGB"})])
        lon, lat = self.to_wgs.transform(*(self.origin + [9500, 0]))
        self.result.update(status="INFEASIBLE", sorties=[], metrics={}, diagnosis={"proof": {
            "type": "unreachable_required_point", "job_id": "JOB", "point": [lon, lat]}})
        report = self.check()
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["metrics"]["proof_scope"], "entire_required_job")

    def test_aircraft_service_interval_is_enforced(self):
        second = self.sortie(offset=70)
        second.update(id="FLIGHT_2", index=1)
        self.result["sorties"].append(second)
        self.result["metrics"].update(distance_m=800, total_flight_s=120, makespan_s=130)
        self.assertIn("AIRCRAFT_SERVICE", self.codes())

    def test_physical_swath_does_not_use_reported_coverage(self):
        self.payload["camera"]["image_width_px"] = 1000
        self.write("payload_catalog.json", {"payload_profiles": [self.payload]})
        self.assertIn("COVERAGE_GAP", self.codes())

    def test_zero_buffer_point_obstacle_is_not_erased(self):
        self.layer("obstacles.geojson", [(Point(0, 0), {"id": "POINT_TOWER", "height_m": 110,
                                                      "horizontal_buffer_m": 0, "vertical_buffer_m": 10})])
        self.assertIn("OBSTACLE_CLEARANCE", self.codes())

    def test_raster_cell_grazing_is_checked_between_regular_samples(self):
        raster = np.zeros((10, 10), dtype="float32")
        affine = from_origin(self.origin[0], self.origin[1] + 300, 30, 30)
        raster[9, 1] = 20
        with rasterio.open(self.directory / "dem.tif", "w", driver="GTiff", height=10, width=10,
                           count=1, dtype="float32", crs="EPSG:32637", transform=affine) as dataset:
            dataset.write(raster, 1)
        scene = load_scene(self.directory)
        start = np.r_[self.origin + [29.1, .1], 10]
        end = np.r_[self.origin + [31.1, 60.1], 10]
        segment = Segment(start, end, 0, 6, 10, 10, "transit", None, "U1", "F", 0)
        self.assertLess(float(np.min(scene.terrain_clearances(segment))), 0)

    def test_unsupported_temporal_agl_reference_fails_closed(self):
        self.write("metadata.json", {"scenario_id": "test", "metric_crs": "EPSG:32637", "temporal_altitude_reference": "AGL"})
        self.assertIn("INVALID_DATA", self.codes())

    def test_report_discloses_model_limits_for_both_statuses(self):
        report = self.check()
        self.assertIn("aircraft_dynamics", report["not_checked"])
        self.assertIn("not an operational flight certificate", report["model_scope"])
        self.result.update(status="INFEASIBLE", sorties=[], metrics={}, diagnosis={"proof": {
            "type": "no_compatible_uav", "job_id": "JOB"}})
        self.assertIn("global_optimality", self.check()["not_checked"])

    def test_job_type_cannot_be_replaced_with_incompatible_payload(self):
        self.layer("survey_areas.geojson", [(box(-80, -20, 80, 20), {
            "id": "JOB", "survey_type": "thermal", "payload_profile": "RGB"})])
        self.assertIn("INVALID_DATA", self.codes())


if __name__ == "__main__":
    unittest.main()
