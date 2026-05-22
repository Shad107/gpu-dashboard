"""HTTP handlers for the air-gap audit endpoints (R&D #12.7)."""
from __future__ import annotations

from typing import Optional, Tuple


Response = Tuple[int, dict]


def handle_airgap_status(ctx: dict) -> Response:
    """Return air-gap mode status + counters."""
    from ..modules import airgap
    cfg = ctx.get("config")
    return 200, {"ok": True, **airgap.status(cfg)}


def handle_airgap_audit(ctx: dict, params: Optional[dict] = None) -> Response:
    """List recent blocked outbound attempts.

    Query params :
      limit = max number of entries to return (default 100, max 200)
    """
    from ..modules import airgap
    params = params or {}
    try:
        limit = max(1, min(200, int(params.get("limit", "100"))))
    except (ValueError, TypeError):
        limit = 100
    return 200, {"ok": True, "count": len(airgap.get_audit(limit)),
                 "blocked": airgap.get_audit(limit)}
