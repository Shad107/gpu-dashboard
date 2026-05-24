"""HTTP handler — R&D #85.2 extcon state auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_extcon_state_audit_status(ctx: dict) -> Response:
    from ..modules import extcon_state_audit
    return 200, extcon_state_audit.status(ctx.get("config"))
