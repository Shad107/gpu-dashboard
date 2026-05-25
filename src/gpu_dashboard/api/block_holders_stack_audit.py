"""HTTP handler — R&D #96.4 block holders/dm-stack auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_block_holders_stack_audit_status(
        ctx: dict) -> Response:
    from ..modules import block_holders_stack_audit
    return 200, block_holders_stack_audit.status(
        ctx.get("config"))
