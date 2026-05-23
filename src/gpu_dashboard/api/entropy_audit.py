"""HTTP handler for /api/entropy-audit (R&D #45.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_entropy_audit_status(ctx: dict) -> Response:
    from ..modules import entropy_audit
    return 200, entropy_audit.status(ctx.get("config"))
