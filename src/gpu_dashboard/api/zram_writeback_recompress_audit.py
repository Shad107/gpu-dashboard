"""HTTP handler — R&D #103.3 zram writeback/recompress auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_zram_writeback_recompress_audit_status(
        ctx: dict) -> Response:
    from ..modules import zram_writeback_recompress_audit
    return 200, zram_writeback_recompress_audit.status(
        ctx.get("config"))
