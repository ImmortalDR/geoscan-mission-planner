# H1 sweep angle report — `S01_full_customer_acceptance_100km2`

- **Coverage:** 98.35%
- **Tasks:** 110
- **Gate B pass (≥99.9%):** no

## Coverage badge 🔴 `98.35%`

## Job `JOB_RGB` — top candidates

| selected | label | angle_deg | survey_m | turns | score | FW viol |
|---|---|---:|---:|---:|---:|---:|
| ✓ | corridor_along | 90.0 | 359424.0 | 35 | 401449.7 | 0 |
|  | wind_perpendicular | 110.0 | 365945.7 | 96 | 448540.7 | 0 |
|  | grid_60 | 60.0 | 365947.4 | 123 | 452265.0 | 0 |

## Job `JOB_MS` — top candidates

| selected | label | angle_deg | survey_m | turns | score | FW viol |
|---|---|---:|---:|---:|---:|---:|
| ✓ | corridor_along | 90.0 | 98425.3 | 18 | 126460.6 | 0 |
|  | corridor_across | 0.0 | 105464.1 | 58 | 146142.1 | 0 |
|  | wind_aligned | 20.0 | 105833.8 | 60 | 149009.8 | 0 |

## Job `JOB_GEO` — top candidates

| selected | label | angle_deg | survey_m | turns | score | FW viol |
|---|---|---:|---:|---:|---:|---:|
| ✓ | corridor_along | 90.0 | 323536.9 | 69 | 387771.0 | 0 |
|  | wind_perpendicular | 110.0 | 353762.9 | 1705 | 714059.8 | 0 |
|  | grid_60 | 60.0 | 353796.1 | 2459 | 739167.6 | 0 |

## Job `JOB_LIDAR` — top candidates

| selected | label | angle_deg | survey_m | turns | score | FW viol |
|---|---|---:|---:|---:|---:|---:|
| ✓ | corridor_along | 90.0 | 345504.8 | 51 | 386077.8 | 0 |
|  | wind_aligned | 20.0 | 361977.9 | 379 | 403123.9 | 0 |
|  | grid_30 | 30.0 | 362040.7 | 357 | 407026.5 | 0 |

## Job `JOB_IR` — top candidates

| selected | label | angle_deg | survey_m | turns | score | FW viol |
|---|---|---:|---:|---:|---:|---:|
| ✓ | corridor_along | 90.0 | 55776.0 | 8 | 68962.8 | 0 |
|  | corridor_across | 0.0 | 55400.0 | 29 | 79788.3 | 0 |
|  | grid_30 | 30.0 | 56652.9 | 29 | 82782.6 | 0 |

## Warnings

- job JOB_GEO: 1 hole(s) preserved
- site BASE_W: outside survey area (+50m slack)
- site BASE_E: outside survey area (+50m slack)
- site BASE_N: outside survey area (+50m slack)
- M1: 3 obstacle(s) loaded as soft exclusion
- M1: 2 temporal airspace zone(s) loaded (H1 does not schedule around windows; H2/H3)
- job JOB_RGB: corridor mode — prefer along/across angles
- job JOB_MS: corridor mode — prefer along/across angles
- job JOB_GEO: corridor mode — prefer along/across angles
- job JOB_LIDAR: corridor mode — prefer along/across angles
- job JOB_IR: corridor mode — prefer along/across angles
- Gate B: coverage_percent=98.35 < required 99.9
- M3: task JOB_RGB#T000 ground relief 59.6m vs AGL 123m (consider terrain-following in autopilot)
- M3: task JOB_RGB#T000 local AGL swing 59.6m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T001 ground relief 59.3m vs AGL 123m (consider terrain-following in autopilot)
- M3: task JOB_RGB#T001 local AGL swing 59.3m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T002 ground relief 58.7m vs AGL 123m (consider terrain-following in autopilot)
- M3: task JOB_RGB#T002 local AGL swing 58.7m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T003 ground relief 57.9m vs AGL 123m (consider terrain-following in autopilot)
- M3: task JOB_RGB#T003 local AGL swing 57.9m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T004 ground relief 57.4m vs AGL 123m (consider terrain-following in autopilot)
- M3: task JOB_RGB#T004 local AGL swing 57.4m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T005 ground relief 56.2m vs AGL 123m (consider terrain-following in autopilot)
- M3: task JOB_RGB#T005 local AGL swing 56.2m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T006 ground relief 54.8m vs AGL 123m (consider terrain-following in autopilot)
- M3: task JOB_RGB#T006 local AGL swing 54.8m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T007 ground relief 53.2m vs AGL 123m (consider terrain-following in autopilot)
- M3: task JOB_RGB#T007 local AGL swing 53.2m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T008 ground relief 51.4m vs AGL 123m (consider terrain-following in autopilot)
- M3: task JOB_RGB#T008 local AGL swing 51.4m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T009 ground relief 50.5m vs AGL 123m (consider terrain-following in autopilot)
- M3: task JOB_RGB#T009 local AGL swing 50.5m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T010 ground relief 48.4m vs AGL 123m (consider terrain-following in autopilot)
- M3: task JOB_RGB#T010 local AGL swing 48.4m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T011 ground relief 46.2m vs AGL 123m (consider terrain-following in autopilot)
- M3: task JOB_RGB#T011 local AGL swing 46.2m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T012 ground relief 44.0m vs AGL 123m (consider terrain-following in autopilot)
- M3: task JOB_RGB#T012 local AGL swing 44.0m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T013 ground relief 41.6m vs AGL 123m (consider terrain-following in autopilot)
- M3: task JOB_RGB#T013 local AGL swing 41.6m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T014 ground relief 40.4m vs AGL 123m (consider terrain-following in autopilot)
- M3: task JOB_RGB#T014 local AGL swing 40.4m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T015 ground relief 38.0m vs AGL 123m (consider terrain-following in autopilot)
- M3: task JOB_RGB#T015 local AGL swing 38.0m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T016 ground relief 35.5m vs AGL 123m (consider terrain-following in autopilot)
- M3: task JOB_RGB#T016 local AGL swing 35.5m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T017 ground relief 33.1m vs AGL 123m (consider terrain-following in autopilot)
- M3: task JOB_RGB#T017 local AGL swing 33.1m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T018 local AGL swing 30.7m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T019 local AGL swing 29.5m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T020 local AGL swing 27.3m (GSD/overlap will drift on hills)
- M3: task JOB_RGB#T021 local AGL swing 25.1m (GSD/overlap will drift on hills)
- M3: task JOB_MS#T006 local AGL swing 66.3m (GSD/overlap will drift on hills)
- M3: task JOB_MS#T009 local AGL swing 66.9m (GSD/overlap will drift on hills)
- M3: task JOB_MS#T010 local AGL swing 65.7m (GSD/overlap will drift on hills)
- M3: task JOB_MS#T018 local AGL swing 68.2m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T000 LOCAL_AGL_BELOW_MIN min_local_agl=39.7m < 40m (ref_z=166.8m, planned_agl=80m) — raise AGL or follow terrain
- M3: task JOB_GEO#T000 ground relief 47.3m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T000 local AGL swing 47.3m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T001 ground relief 44.3m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T001 local AGL swing 44.3m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T002 ground relief 41.1m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T002 local AGL swing 41.1m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T003 ground relief 27.5m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T003 local AGL swing 27.5m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T004 ground relief 35.6m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T004 local AGL swing 35.6m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T005 ground relief 33.9m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T005 local AGL swing 33.9m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T006 ground relief 26.7m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T006 local AGL swing 26.7m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T007 ground relief 28.1m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T007 local AGL swing 28.1m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T008 ground relief 26.7m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T008 local AGL swing 26.7m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T009 ground relief 27.3m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T009 local AGL swing 27.3m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T010 ground relief 25.4m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T010 local AGL swing 25.4m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T011 ground relief 26.5m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T011 local AGL swing 26.5m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T012 ground relief 25.2m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T012 local AGL swing 25.2m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T013 ground relief 32.4m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T013 local AGL swing 32.4m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T014 ground relief 34.5m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T014 local AGL swing 34.5m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T015 ground relief 59.9m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T015 local AGL swing 59.9m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T016 ground relief 62.6m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T016 local AGL swing 62.6m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T017 ground relief 63.7m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T017 local AGL swing 63.7m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T018 ground relief 60.0m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T018 local AGL swing 60.0m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T019 ground relief 54.6m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T019 local AGL swing 54.6m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T020 ground relief 28.3m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T020 local AGL swing 28.3m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T021 ground relief 24.5m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T021 local AGL swing 24.5m (GSD/overlap will drift on hills)
- M3: task JOB_GEO#T022 ground relief 24.0m vs AGL 80m (consider terrain-following in autopilot)
- M3: task JOB_GEO#T022 local AGL swing 24.0m (GSD/overlap will drift on hills)
- M3: task JOB_LIDAR#T000 ground relief 32.7m vs AGL 120m (consider terrain-following in autopilot)
- M3: task JOB_LIDAR#T000 local AGL swing 32.7m (GSD/overlap will drift on hills)
- M3: task JOB_LIDAR#T001 ground relief 36.7m vs AGL 120m (consider terrain-following in autopilot)
- M3: task JOB_LIDAR#T001 local AGL swing 36.7m (GSD/overlap will drift on hills)
- M3: task JOB_LIDAR#T002 ground relief 39.8m vs AGL 120m (consider terrain-following in autopilot)
- M3: task JOB_LIDAR#T002 local AGL swing 39.8m (GSD/overlap will drift on hills)
- M3: task JOB_LIDAR#T003 ground relief 43.8m vs AGL 120m (consider terrain-following in autopilot)
- M3: task JOB_LIDAR#T003 local AGL swing 43.8m (GSD/overlap will drift on hills)
- M3: task JOB_LIDAR#T004 ground relief 46.5m vs AGL 120m (consider terrain-following in autopilot)
- M3: task JOB_LIDAR#T004 local AGL swing 46.5m (GSD/overlap will drift on hills)
- M3: task JOB_LIDAR#T005 ground relief 50.0m vs AGL 120m (consider terrain-following in autopilot)
- M3: task JOB_LIDAR#T005 local AGL swing 50.0m (GSD/overlap will drift on hills)
- M3: task JOB_LIDAR#T006 ground relief 52.5m vs AGL 120m (consider terrain-following in autopilot)
- M3: task JOB_LIDAR#T006 local AGL swing 52.5m (GSD/overlap will drift on hills)
- M3: task JOB_LIDAR#T007 ground relief 55.1m vs AGL 120m (consider terrain-following in autopilot)
- M3: task JOB_LIDAR#T007 local AGL swing 55.1m (GSD/overlap will drift on hills)
- M3: task JOB_LIDAR#T008 ground relief 54.2m vs AGL 120m (consider terrain-following in autopilot)
- M3: task JOB_LIDAR#T008 local AGL swing 54.2m (GSD/overlap will drift on hills)
- M3: task JOB_LIDAR#T009 ground relief 57.3m vs AGL 120m (consider terrain-following in autopilot)
- M3: task JOB_LIDAR#T009 local AGL swing 57.3m (GSD/overlap will drift on hills)
- M3: task JOB_LIDAR#T011 ground relief 55.4m vs AGL 120m (consider terrain-following in autopilot)
- M3: task JOB_LIDAR#T011 local AGL swing 55.4m (GSD/overlap will drift on hills)
- M3: task JOB_LIDAR#T012 ground relief 57.9m vs AGL 120m (consider terrain-following in autopilot)
- M3: task JOB_LIDAR#T012 local AGL swing 57.9m (GSD/overlap will drift on hills)
- M3: task JOB_LIDAR#T014 ground relief 53.1m vs AGL 120m (consider terrain-following in autopilot)
- M3: task JOB_LIDAR#T014 local AGL swing 53.1m (GSD/overlap will drift on hills)
- M3: task JOB_LIDAR#T015 ground relief 53.4m vs AGL 120m (consider terrain-following in autopilot)
- M3: task JOB_LIDAR#T015 local AGL swing 53.4m (GSD/overlap will drift on hills)
- M3: task JOB_LIDAR#T016 ground relief 52.7m vs AGL 120m (consider terrain-following in autopilot)
- M3: task JOB_LIDAR#T016 local AGL swing 52.7m (GSD/overlap will drift on hills)
- M3: task JOB_LIDAR#T017 ground relief 48.3m vs AGL 120m (consider terrain-following in autopilot)
- M3: task JOB_LIDAR#T017 local AGL swing 48.3m (GSD/overlap will drift on hills)
- M3: task JOB_LIDAR#T018 ground relief 46.7m vs AGL 120m (consider terrain-following in autopilot)
- M3: task JOB_LIDAR#T018 local AGL swing 46.7m (GSD/overlap will drift on hills)
- M3: task JOB_LIDAR#T019 ground relief 41.7m vs AGL 120m (consider terrain-following in autopilot)
- M3: task JOB_LIDAR#T019 local AGL swing 41.7m (GSD/overlap will drift on hills)
- M3: task JOB_LIDAR#T020 ground relief 37.7m vs AGL 120m (consider terrain-following in autopilot)
- M3: task JOB_LIDAR#T020 local AGL swing 37.7m (GSD/overlap will drift on hills)

