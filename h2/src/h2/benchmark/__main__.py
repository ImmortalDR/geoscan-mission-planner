"""Run one or many local Solomon/Homberger files and write auditable JSON."""
import argparse
import json
from pathlib import Path
from .vrptw import parse, solve
from .report import write_html


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('instances', nargs='+', type=Path)
    parser.add_argument('--time-limit', type=float, default=1.0, help='seconds per instance')
    parser.add_argument('--method', choices=['baseline', 'guided_local_search'], default='guided_local_search')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--html-dir', type=Path, help='optional route and window HTML reports')
    args = parser.parse_args()
    results = []
    for p in args.instances:
        instance = parse(p)
        result = solve(instance, args.time_limit, args.method)
        results.append(result.to_dict())
        if args.html_dir:
            write_html(instance, result, args.html_dir / (p.stem + '.html'))
    rendered = json.dumps({'profile': 'vrptw_standard', 'results': results}, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered+'\n')
    else:
        print(rendered)
    return 0 if all(r['metrics']['feasible'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
