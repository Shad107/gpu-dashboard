"""HTTP handler — Hardening #2 collection profile.

Accepts optional query-string overrides for the verdict budgets,
added in Hardening #12:

  ?slow_module_ms=N   per-module budget (default 500 ms)
  ?slow_total_ms=N    per-fleet optimizable-total budget
                      (default 5000 ms)

Invalid / out-of-range values are silently ignored and the
defaults are kept. Reasonable bounds enforced: both fields must
parse as a positive float and stay under 10 minutes.
"""
from __future__ import annotations

from typing import Any, Optional, Tuple

Response = Tuple[int, Any]


_MAX_BUDGET_MS = 600_000.0  # 10 minutes


def _parse_budget(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v <= 0 or v > _MAX_BUDGET_MS:
        return None
    return v


def handle_collection_profile_audit_status(
        ctx: dict,
        params: Optional[dict] = None) -> Response:
    from ..modules import collection_profile_audit
    params = params or {}
    kwargs: dict = {}
    m = _parse_budget(params.get("slow_module_ms"))
    if m is not None:
        kwargs["slow_module_ms"] = m
    t = _parse_budget(params.get("slow_total_ms"))
    if t is not None:
        kwargs["slow_total_ms"] = t
    return 200, collection_profile_audit.status(
        ctx.get("config"), **kwargs)
