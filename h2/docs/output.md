# H2 → H3

`h2.plan.v1` is an H2 serialization, not a modification of the H1 bundle or of
existing `gmp.models` entities. H3 must agree the handoff; no certificate emitted.

| Field | Meaning |
|---|---|
| `input_schema_version`, `input_sha256` | Frozen H1 version and canonical JSON SHA-256 |
| `scene_id`, `crs`, `tasks` | Preserved H1 data, tasks as a list |
| `time_origin` | Explicit ISO-8601 origin; all `*_s` relative to it |
| `sorties` | id/uav_id/index, sites, ordered task_ids and task_reversed |
| `waypoints` | metric x/y, agl_m, z_m, t_s, phase, task_id |
| `task_times` | task_id, start_s, end_s; separate from immutable H1 tasks |
| `unassigned` | Explicit task_id/reason for every task omitted by search |
| `infeasibility_proofs` | Sufficient resource/site/window bounds under the declared model |
| `metrics` | makespan_s, total_flight_s, distance, counts, runtime, iterations |
| `annealing` | Optional: fixed seed, depth, iterations, estimated/final metric, selected source, candidate checks, stage timings and solution SHA-256 |
| `routing` | Routing search, graph/model/validation timings, fixed seed, finalists and solution SHA-256 |
| `routing_graph` | GeoJSON physical graph per profile, survey directions, terminal dummy links |
| `solver_log` | Incumbents; stage, elapsed_s, objective_value, unassigned_count |
| `checks` | Independent H2 checks and their scope; not SafetyReport |
| `deconfliction` | Before and remaining conflicts |
| `assumptions` | Missing geography/terrain and resource-model boundaries |
| `requires_h3_validation` | Always true |

With `h3` installed or on PYTHONPATH:

```python
from h2.contract import load_bundle
from h2.planner import plan_bundle
from h2.integration import to_gmp_plan

bundle = load_bundle("fixtures/S00_smoke_rgb.bundle.json")
h2_result = plan_bundle(bundle)
gmp_plan = to_gmp_plan(bundle, h2_result)
# Existing H3 receives scene + gmp_plan and performs its own validation.
# gmp_plan.status == "UNKNOWN"; certificate is None.
```

The adapter imports existing gmp models lazily. There is no vendored coverage,
H1 model implementation or H3 validator. Survey coordinates, reversals and
timings survive conversion; resource remaining is recalculated from elapsed
flight. Coverage percentage is not fabricated by H2.

The following site rules describe legacy inputs without `refuel_sites`.
Explicit `refuel_sites` uses mission-level start/end and separate intermediate
permissions; see [routing.md](routing.md#промежуточная-зарядка).

`start_site` pins the initial physical location when set. When it is `null`,
H2 chooses any non-candidate site whose role is `start` or `both`.
`landing_site`, when set, is mandatory; when it is `null`, H2 chooses among
non-candidate `landing`, `both`, and `reserve` sites. Subsequent sorties
start where the previous sortie landed, so an intermediate landing must also
permit takeoff. `allow_different_start_end=false` still forces return to the
launch site. Candidate sites are not activated automatically. Service is a
ground gap and does not count as flight time; neither does delayed departure.

The legacy mode (`Settings.search_depth=None` or CLI `--legacy`) uses CP-SAT/LNS.
The default web/CLI pipeline uses [Routing Solver](routing.md) with immutable
individual transects (`gmp.h1_h2.v3`). Python `Settings` retains the old default
for compatibility; pass `algorithm="routing"` explicitly. CLI `--algorithm annealing`
selects the previous simulated annealing implementation.

Legacy initial construction processes every mandatory task even when the improvement
budget expires. It extends the last sortie or adds a new sortie after ground
service, rather than repartitioning the whole flight history on each insertion.
The remaining time budget bounds CP-SAT/LNS improvement. Cancellation/process
limits still apply. More sorties never relax endurance, site roles, task
indivisibility or the mission window. An incomplete schedule remains UNRESOLVED
(or INFEASIBLE with a proof); unassigned tasks include construction attempts
with resource and mission-window details when available.
