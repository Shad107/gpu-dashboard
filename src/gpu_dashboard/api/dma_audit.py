"""HTTP handler for /api/dma-audit (R&D #48.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_dma_audit_status(ctx: dict) -> Response:
    from ..modules import dma_audit
    return 200, dma_audit.status(ctx.get("config"))
