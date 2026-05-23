"""HTTP handler for /api/pstate-audit (R&D #21.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_pstate_audit_status(ctx: dict) -> Response:
    from ..modules import pstate_audit
    return 200, pstate_audit.status(ctx.get("config"))
