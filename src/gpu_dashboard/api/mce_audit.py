"""HTTP handler for /api/mce-audit (R&D #47.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_mce_audit_status(ctx: dict) -> Response:
    from ..modules import mce_audit
    return 200, mce_audit.status(ctx.get("config"))
