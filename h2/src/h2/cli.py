"""Reproducible H2 CLI; no H1 regeneration or H3 certificate issuance."""
import argparse
import json
from pathlib import Path
import sys

from .contract import load_bundle
from .planner import plan_bundle
from .scheduler import Settings


def save_plan(plan, directory):
    from .report import write_html, export_geojson
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory/"plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    (directory/"routes.geojson").write_text(json.dumps(export_geojson(plan), ensure_ascii=False, allow_nan=False)+"\n")
    write_html(plan, directory/"report.html")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate-bundle")
    validate.add_argument("bundle", type=Path)
    plan = sub.add_parser("plan")
    plan.add_argument("bundle", type=Path)
    plan.add_argument("--scene", type=Path)
    estimate = sub.add_parser("estimate", help="Estimate an explicitly supplied fixed plan")
    estimate.add_argument("bundle", type=Path)
    estimate.add_argument("--scene", type=Path)
    estimate.add_argument("--fixed-plan", type=Path, required=True)
    estimate.add_argument("--out", type=Path, required=True)
    estimate.add_argument("--grid-step", type=float, default=100.)
    estimate.add_argument("--survey-speed-factor", type=float, default=.75)
    estimate.add_argument("--materialize", action="store_true")
    adapted = sub.add_parser("vrptw-adapted")
    adapted.add_argument("instance", type=Path)
    adapted.add_argument("--fleet-size", type=int, default=3)
    adapted.add_argument("--customers", type=int, default=10)
    for command in (plan, adapted):
        command.add_argument("--algorithm", choices=["routing", "annealing"], default="routing")
        command.add_argument("--search-depth", choices=["quick","standard","deep"], default="standard")
        command.add_argument("--legacy", action="store_true", help="Use the previous CP-SAT/LNS planner")
        command.add_argument("--objective", choices=["makespan","total_flight"], default="makespan")
        command.add_argument("--time-budget", type=float, default=60.)
        command.add_argument("--iterations", type=int, default=200, help="Legacy LNS limit; annealing uses --search-depth")
        command.add_argument("--seed", type=int, default=20260918)
        command.add_argument("--no-cpsat", action="store_true")
        command.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-bundle":
            b = load_bundle(args.bundle)
            print(json.dumps(dict(valid=True,scene_id=b["scene_id"],tasks=len(b["tasks"]))))
            return 0
        if args.command == "estimate":
            from .fixed_estimator import FixedPlanEstimator
            from .estimate_grid import EstimateConfig
            bundle=load_bundle(args.bundle)
            scene=json.loads(args.scene.read_text()) if args.scene else None
            fixed=json.loads(args.fixed_plan.read_text())
            evaluator=FixedPlanEstimator(bundle,scene,Settings(survey_speed_factor=args.survey_speed_factor),
                                         EstimateConfig(grid_step_m=args.grid_step))
            result=evaluator.evaluate(fixed)
            args.out.mkdir(parents=True,exist_ok=True)
            (args.out/"preparation.json").write_text(json.dumps(evaluator.report(),indent=2,allow_nan=False)+"\n")
            (args.out/"estimate.json").write_text(json.dumps(result,indent=2,allow_nan=False)+"\n")
            if args.materialize:
                save_plan(evaluator.materialize(fixed),args.out/"detailed")
            print(json.dumps({k:result[k] for k in ('status','preparation_s','evaluation_s','makespan_s','total_flight_s')}))
            return 0 if result['status']=='ESTIMATED_FEASIBLE' else 2
        scene, side = None, {}
        if args.command == "vrptw-adapted":
            from .benchmark.vrptw import parse
            from .benchmark.uav_adapter import adapt
            bundle, side = adapt(parse(args.instance), args.fleet_size, args.customers, args.seed)
        else:
            bundle = load_bundle(args.bundle)
            if args.scene:
                scene = json.loads(args.scene.read_text())
        options = Settings(algorithm="annealing" if args.legacy else args.algorithm, search_depth=None if args.legacy else args.search_depth, objective=args.objective,time_budget_s=args.time_budget,
                           max_iterations=args.iterations,seed=args.seed,cpsat=not args.no_cpsat,**side)
        result = plan_bundle(bundle,options,scene)
        save_plan(result,args.out)
        if args.command == "vrptw-adapted":
            (args.out/"input.bundle.json").write_text(json.dumps(bundle,indent=2)+"\n")
            (args.out/"side_inputs.json").write_text(json.dumps(side,indent=2)+"\n")
        print(json.dumps(dict(status=result["status"],metrics=result["metrics"],
                              violations=result["checks"]["violations"], report=str(args.out/"report.html")),ensure_ascii=False))
        return 0 if result["status"] == "FEASIBLE" else 2
    except (ValueError, OSError, KeyError, ImportError) as exc:
        print(f"H2 input/error: {exc}",file=sys.stderr)
        return 1
