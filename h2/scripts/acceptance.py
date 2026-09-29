"""Record outcomes on immutable H1 snapshots (not all scenes are feasible)."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from h2.contract import load_bundle
from h2.planner import plan_bundle, Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=float, default=10.0)
    parser.add_argument("--output", type=Path, default=Path("artifacts/h1_acceptance.json"))
    parser.add_argument(
        "--scenes",
        nargs="*",
        default=None,
        help="Optional scene_id substrings; default = all S*.bundle.json",
    )
    args = parser.parse_args()
    rows = []
    root = Path(__file__).resolve().parents[1] / "fixtures"
    paths = sorted(root.glob("S*.bundle.json"))
    if args.scenes:
        want = set(args.scenes)
        paths = [p for p in paths if any(s in p.stem for s in want)]
    for path in paths:
        bundle = load_bundle(path)
        result = plan_bundle(
            bundle,
            Settings(
                objective=bundle["mission"]["objectives"][0],
                time_budget_s=args.budget,
                max_iterations=30,
            ),
        )
        row = dict(
            scene=bundle["scene_id"],
            input_sha256=result["input_sha256"],
            status=result["status"],
            metrics=result["metrics"],
            unassigned=result["unassigned"],
            violations=result["checks"]["violations"],
            infeasibility_proofs=result["infeasibility_proofs"],
            assumptions=result["assumptions"],
        )
        rows.append(row)
        print(row["scene"], row["status"], "unassigned:", len(row["unassigned"]), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    main()
