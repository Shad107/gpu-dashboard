"""HTTP handler for /api/rebar-audit (R&D #27.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_rebar_audit_status(ctx: dict) -> Response:
    from ..modules import rebar_audit
    return 200, rebar_audit.status(ctx.get("config"))
