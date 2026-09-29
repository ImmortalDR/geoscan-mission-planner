"""W-07 angle comparison report (markdown)."""
from __future__ import annotations

from pathlib import Path

from ..coverage.engine import CoverageResult
from ..models import Scene


def export_angle_report(scene: Scene, coverage: CoverageResult, path: str | Path) -> Path:
    lines = [
        f"# H1 sweep angle report — `{scene.id}`",
        "",
        f"- **Coverage:** {coverage.coverage_percent:.2f}%",
        f"- **Tasks:** {len(coverage.tasks)}",
        f"- **Gate B pass (≥99.9%):** {'yes' if coverage.coverage_percent + 1e-6 >= 99.9 else 'no'}",
        "",
    ]
    color = "🟢" if coverage.coverage_percent + 1e-6 >= 99.9 else "🔴"
    lines.append(f"## Coverage badge {color} `{coverage.coverage_percent:.2f}%`")
    lines.append("")
    for job_id, cands in (coverage.candidates or {}).items():
        lines.append(f"## Job `{job_id}` — top candidates")
        lines.append("")
        lines.append("| selected | label | angle_deg | survey_m | turns | score | FW viol |")
        lines.append("|---|---|---:|---:|---:|---:|---:|")
        for c in cands:
            sel = "✓" if c.get("selected") else ""
            # turn_count may be absent in compact report
            lines.append(
                f"| {sel} | {c.get('label')} | {c.get('angle_deg')} | "
                f"{c.get('survey_length_m')} | {c.get('turn_count', '—')} | "
                f"{c.get('score')} | {c.get('maneuver_violations')} |"
            )
        lines.append("")
    if coverage.warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in coverage.warnings:
            lines.append(f"- {w}")
        lines.append("")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
