"""HTTP handler for /api/dma-heap-audit (R&D #74.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_dma_heap_audit_status(ctx: dict) -> Response:
    from ..modules import dma_heap_audit
    return 200, dma_heap_audit.status(ctx.get("config"))
