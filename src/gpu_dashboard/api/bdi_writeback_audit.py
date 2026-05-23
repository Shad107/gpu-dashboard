"""HTTP handler for /api/bdi-writeback-audit (R&D #56.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_bdi_writeback_audit_status(ctx: dict) -> Response:
    from ..modules import bdi_writeback_audit
    return 200, bdi_writeback_audit.status(ctx.get("config"))
