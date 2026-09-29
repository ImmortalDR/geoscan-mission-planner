"""Run the full planning pipeline on one conformance scenario."""
import argparse, json, sys, time
sys.path.insert(0, "/root/h3/src")
from gmp.io.scene_loader import load_scene, validate_scene
from gmp.planner import PlannerOptions, plan_mission

p = argparse.ArgumentParser()
p.add_argument("scenario")
p.add_argument("--budget", type=float, default=20.0)
p.add_argument("--objective", default=None)
p.add_argument("--no-recommend", action="store_true")
p.add_argument("--verbose", action="store_true")
a = p.parse_args()

root = "/root/h3/datasets/geoscan_customer_conformance"
sc = load_scene(f"{root}/{a.scenario}")
v = validate_scene(sc)
print("scene ok:", v["ok"], "issues:", [i["code"] for i in v["issues"]])
obj = a.objective or sc.mission.objective
t0 = time.time()
cb = (lambda e: print("   ..", json.dumps(e, ensure_ascii=False)[:200])) if a.verbose else None
plan = plan_mission(sc, PlannerOptions(objective=obj, time_budget_s=a.budget, recommend=not a.no_recommend), progress_cb=cb)
print(f"== {a.scenario} objective={obj} status={plan.status} in {time.time()-t0:.1f}s")
m = plan.metrics
print(json.dumps({k: m.get(k) for k in ("coverage_percent","coverage_complete","makespan_min","total_flight_min","sortie_count","used_uav_count","assigned_task_count","dropped_task_count","min_resource_margin_percent","window_exceeded")}, ensure_ascii=False, indent=1))
print("validator counters:", json.dumps(plan.validation["counters"], ensure_ascii=False))
print("certificate:", bool(plan.certificate))
if plan.deconfliction: print("deconfliction:", json.dumps({k:plan.deconfliction[k] for k in ("initial_conflict_count","final_conflict_count","rounds","ladder_used")}, ensure_ascii=False))
if plan.diagnosis: print("diagnosis:", json.dumps(plan.diagnosis["reason_codes"], ensure_ascii=False))
for r in plan.recommendations: print(f"  REC {r['rank']} {r['type']} verified={r['verified']} -> {json.dumps(r['verification'], ensure_ascii=False)[:220]}")
for v_ in plan.validation["violations"][:5]: print("  VIOL", v_["code"], v_["message"][:160])
