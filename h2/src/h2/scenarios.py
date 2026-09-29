"""Small deterministic synthetic H2 scenarios (not H1 coverage generation)."""
from copy import deepcopy
import math


def make_bundle(n=3, fleet_size=2, endurance_min=30):
    fleet = [dict(id=f"u{i}", model=["geoscan_201", "geoscan_401", "geoscan_701", "geoscan_801", "geoscan_gemini"][i%5],
                  uav_class="multirotor", ground_speed_ms=10., operational_endurance_min=endurance_min,
                  max_wind_ms=10., payload_classes=["rgb"], energy_reserve_fraction=.2,
                  horizontal_separation_m=10., vertical_separation_m=10., turnaround_buffer_m=0.,
                  start_site="base", landing_site="base", takeoff_time_s=5., landing_time_s=5.,
                  service_time_s=30., cruise_agl_m=60.) for i in range(fleet_size)]
    tasks = []
    for i in range(n):
        coords = [[500100.,6000000.+i*50], [500500.,6000000.+i*50]]
        tasks.append(dict(id=f"t{i}", job_id="job", payload_class="rgb", payload_profile_id="rgb",
                          agl_m=60., transects=[dict(coords=deepcopy(coords),length_m=400.,job_id="job")],
                          survey_length_m=400.,turn_count=0,sweep_angle_deg=0.,entry=coords[0],exit=coords[-1],
                          geom_coords=deepcopy(coords),internal_transition_m=0.,fixed_wing_safe=True,notes=[]))
    return dict(schema_version="gmp.h1_h2.v1", scene_id="synthetic", generated_at="2026-09-20T00:00:00Z",
                source="synthetic",crs=dict(metric_epsg=32637,axis="ENU_metres"),fleet=fleet,
                sites=[dict(id="base",x=500000.,y=6000000.,role="both",candidate=False)],
                mission=dict(objectives=["makespan","total_flight"],window_start="2026-09-20T00:00:00Z",
                             window_end="2026-09-20T08:00:00Z",wind=dict(speed_ms=0.,direction_deg_from=0.),
                             allow_different_start_end=False,require_complete_coverage=True,require_schedule=True),
                tasks=tasks,feasibility=dict(eligible_uav_ids_by_task={t["id"]:[u["id"] for u in fleet] for t in tasks},
                                           ineligible_reasons={}),warnings=[])
