"""Evidence wrapper for alternatives actually replanned by an orchestration layer."""

from pathlib import Path

from gmp.safety.h3_gate import validate_result


def verify_alternative(input_dir: Path, result: dict, changes: dict, plan_id: str | None = None) -> dict | None:
    report = validate_result(input_dir, result, plan_id=plan_id)
    if not report["passed"] or report["status"] != "SAFE" or not report["certificate"]:
        return None
    return {"verified": True, "changes": changes, "plan_id": plan_id,
            "status": "SAFE", "metrics": report["metrics"],
            "input_sha256": report["input_sha256"], "result_sha256": report["result_sha256"],
            "certificate": report["certificate"], "requires_user_selection": True}
