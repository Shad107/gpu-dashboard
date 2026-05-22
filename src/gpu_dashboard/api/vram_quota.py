"""HTTP handlers for the VRAM quota enforcer (R&D #13.3)."""
from __future__ import annotations

from typing import Optional, Tuple


Response = Tuple[int, dict]


def handle_vram_quota_status(ctx: dict) -> Response:
    from ..modules import vram_quota as vq
    return 200, vq.status()


def handle_vram_quota_save(ctx: dict, payload: dict) -> Response:
    """POST /api/vram-quota — replace the full rules list."""
    from ..modules import vram_quota as vq
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "payload must be a dict"}
    rules = payload.get("rules", [])
    if not isinstance(rules, list):
        return 400, {"ok": False, "error": "'rules' must be a list"}
    errors: list = []
    for i, r in enumerate(rules):
        err = vq.validate_rule(r)
        if err:
            errors.append(f"rule[{i}]: {err}")
    if errors:
        return 400, {"ok": False, "errors": errors}
    vq.save_rules(rules)
    return 200, {"ok": True, "count": len(rules)}


def handle_vram_quota_evaluate(ctx: dict, params: Optional[dict] = None) -> Response:
    """Manually trigger an evaluation. ?dry_run=1 (default) avoids
    actually sending signals even for term/kill rules."""
    from ..modules import vram_quota as vq
    params = params or {}
    dry_run = params.get("dry_run", "1") in ("1", "true", "True")
    fires = vq.evaluate(dry_run_global=dry_run)
    return 200, {"ok": True, "dry_run": dry_run, "fires": fires}
