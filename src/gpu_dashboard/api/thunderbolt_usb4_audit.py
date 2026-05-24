"""HTTP handler — R&D #86.2 Thunderbolt/USB4 auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_thunderbolt_usb4_audit_status(ctx: dict) -> Response:
    from ..modules import thunderbolt_usb4_audit
    return 200, thunderbolt_usb4_audit.status(ctx.get("config"))
