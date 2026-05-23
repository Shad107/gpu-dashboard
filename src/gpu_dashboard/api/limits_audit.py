"""HTTP handler for /api/limits-audit (PAM limits memlock)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_limits_audit_status(ctx: dict) -> Response:
    from ..modules import limits_audit
    return 200, limits_audit.status(ctx.get("config"))
