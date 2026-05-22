"""HTTP handlers for the rule engine (R&D #12.4)."""
from __future__ import annotations

from typing import Optional, Tuple


Response = Tuple[int, dict]


def handle_rules_list(ctx: dict) -> Response:
    """Return all configured rules + supported metrics/ops."""
    from ..modules import rule_engine as _re
    return 200, {
        "ok": True,
        "rules": _re.load_rules(),
        "metrics_supported": sorted(_re._SUPPORTED_METRICS),
        "ops_supported": sorted(_re._SUPPORTED_OPS),
        "action_kinds_supported": ["notif", "log", "audit"],
    }


def handle_rules_save(ctx: dict, payload: dict) -> Response:
    """Save the whole rules list. Validates each rule first.

    Payload : {rules: [{id, name, enabled, when, then, cooldown_s}, ...]}
    """
    from ..modules import rule_engine as _re
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "payload must be a dict"}
    rules = payload.get("rules", [])
    if not isinstance(rules, list):
        return 400, {"ok": False, "error": "'rules' must be a list"}
    errors: list = []
    for i, r in enumerate(rules):
        err = _re.validate_rule(r)
        if err:
            errors.append(f"rule[{i}]: {err}")
    if errors:
        return 400, {"ok": False, "errors": errors}
    _re.save_rules(rules)
    return 200, {"ok": True, "count": len(rules)}


def handle_rules_evaluate(ctx: dict, params: Optional[dict] = None) -> Response:
    """Manually trigger an evaluation against the recent sampler buffer.

    Query params :
      dry_run = 1 (default) | 0   if 1, don't actually emit actions
    """
    from ..modules import rule_engine as _re
    params = params or {}
    dry_run = params.get("dry_run", "1") in ("1", "true", "True")
    sampler = ctx.get("sampler")
    samples = []
    if sampler:
        try:
            samples = sampler.snapshot()
        except Exception:
            samples = []
    fires = _re.evaluate_all(samples, dry_run=dry_run)
    return 200, {"ok": True, "dry_run": dry_run, "fires": fires, "samples_count": len(samples)}
