"""HTTP handler for /api/zoneinfo-audit (R&D #43.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_zoneinfo_audit_status(ctx: dict) -> Response:
    from ..modules import zoneinfo_audit
    return 200, zoneinfo_audit.status(ctx.get("config"))
