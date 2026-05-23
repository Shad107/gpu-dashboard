"""HTTP handler for /api/mtd-flash-audit (R&D #66.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_mtd_flash_audit_status(ctx: dict) -> Response:
    from ..modules import mtd_flash_audit
    return 200, mtd_flash_audit.status(ctx.get("config"))
