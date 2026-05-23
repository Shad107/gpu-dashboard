"""HTTP handler for /api/cxl-dax-memory-audit (R&D #70.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_cxl_dax_memory_audit_status(ctx: dict) -> Response:
    from ..modules import cxl_dax_memory_audit
    return 200, cxl_dax_memory_audit.status(ctx.get("config"))
