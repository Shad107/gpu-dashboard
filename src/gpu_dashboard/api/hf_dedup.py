"""HTTP handlers for /api/hf-dedup (R&D #15.3)."""
from __future__ import annotations

from typing import Optional, Tuple


Response = Tuple[int, dict]


def handle_hf_dedup_plan(ctx: dict, params: Optional[dict] = None) -> Response:
    """Scan + build a dedup plan. Read-only — no side effects on disk."""
    from ..modules import hf_dedup
    cfg = ctx.get("config")
    extra_raw = cfg.get("MODELS_DIRS", "") if cfg else ""
    extra = [d.strip() for d in (extra_raw or "").split(",") if d.strip()] or None
    return 200, hf_dedup.build_plan(extra_dirs=extra)


def handle_hf_dedup_execute(ctx: dict, payload: dict) -> Response:
    """Execute a previously-returned plan.

    Payload :
      {plan: [...], dry_run: bool (default True)}
    """
    from ..modules import hf_dedup
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "payload must be a dict"}
    plan = payload.get("plan", [])
    if not isinstance(plan, list):
        return 400, {"ok": False, "error": "'plan' must be a list"}
    dry_run = bool(payload.get("dry_run", True))
    result = hf_dedup.execute_plan(plan, dry_run=dry_run)
    # Persist report for audit trail (works for dry-runs too)
    report = {"dry_run": dry_run, **result, "plan_size": len(plan)}
    saved = hf_dedup.save_report(report)
    result["report_path"] = saved
    return 200, result
