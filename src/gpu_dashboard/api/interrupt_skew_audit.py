"""HTTP handler — R&D #87.4 interrupt skew auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_interrupt_skew_audit_status(ctx: dict) -> Response:
    from ..modules import interrupt_skew_audit
    return 200, interrupt_skew_audit.status(ctx.get("config"))
