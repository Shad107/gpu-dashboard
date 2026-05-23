"""HTTP handler for /api/zswap-zram (R&D #41.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_zswap_zram_audit_status(ctx: dict) -> Response:
    from ..modules import zswap_zram_audit
    return 200, zswap_zram_audit.status(ctx.get("config"))
