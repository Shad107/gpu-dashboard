"""HTTP handler for /api/thp-audit (R&D #34.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_thp_audit_status(ctx: dict) -> Response:
    from ..modules import thp_audit
    return 200, thp_audit.status(ctx.get("config"))
