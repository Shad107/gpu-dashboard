"""HTTP handler — R&D #96.1 block discard caps auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_block_discard_caps_audit_status(
        ctx: dict) -> Response:
    from ..modules import block_discard_caps_audit
    return 200, block_discard_caps_audit.status(
        ctx.get("config"))
