"""HTTP handler — R&D #83.1 block integrity (T10-PI) audit."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_block_integrity_audit_status(ctx: dict) -> Response:
    from ..modules import block_integrity_audit
    return 200, block_integrity_audit.status(ctx.get("config"))
