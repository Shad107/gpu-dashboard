"""HTTP handler for /api/usb-role-switch-audit (R&D #71.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_usb_role_switch_audit_status(ctx: dict) -> Response:
    from ..modules import usb_role_switch_audit
    return 200, usb_role_switch_audit.status(ctx.get("config"))
