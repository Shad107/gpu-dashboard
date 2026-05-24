"""HTTP handler — R&D #91.2 dma_buf bufinfo auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_dma_buf_bufinfo_audit_status(
        ctx: dict) -> Response:
    from ..modules import dma_buf_bufinfo_audit
    return 200, dma_buf_bufinfo_audit.status(
        ctx.get("config"))
