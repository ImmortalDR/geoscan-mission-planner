"""Create reproducible synthetic inputs and corresponding H3 plan fixtures.

Run from repository root: python scripts/demo.py --out artifacts/demo
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from h2.scenarios import make_bundle
from h2.planner import plan_bundle, Settings
from h2.cli import save_plan


def scenarios():
    multi=make_bundle(3,1,endurance_min=3.2)
    multi["scene_id"]="S04_h2_multi_sortie"
    crossing=make_bundle(2,2)
    crossing["scene_id"]="S05_h2_crossing"
    # Same base ensures a genuine conflict including launch occupancy.
    crossing["feasibility"]["eligible_uav_ids_by_task"]={"t0":["u0"],"t1":["u1"]}
    different=make_bundle(1,1)
    different["scene_id"]="S10_h2_different_sites"
    different["mission"]["allow_different_start_end"]=True
    different["sites"].append(dict(id="end",x=500500.,y=6000000.,role="landing",candidate=False))
    different["fleet"][0]["landing_site"]="end"
    return [multi,crossing,different]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--out",type=Path,default=Path("artifacts/demo"))
    args=parser.parse_args()
    summary=[]
    for b in scenarios():
        for objective in b["mission"]["objectives"]:
            p=plan_bundle(b,Settings(objective=objective,time_budget_s=10,max_iterations=30))
            out=args.out/b["scene_id"]/objective
            save_plan(p,out)
            (out/"input.bundle.json").write_text(json.dumps(b,indent=2)+"\n")
            summary.append(dict(scene_id=b["scene_id"],objective=objective,status=p["status"],metrics=p["metrics"]))
    print(json.dumps(summary,indent=2))
    return 0 if all(x["status"] == "FEASIBLE" for x in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
