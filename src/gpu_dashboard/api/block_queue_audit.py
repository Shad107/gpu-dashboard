"""HTTP handler for /api/block-queue-audit (R&D #43.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_block_queue_audit_status(ctx: dict) -> Response:
    from ..modules import block_queue_audit
    return 200, block_queue_audit.status(ctx.get("config"))
