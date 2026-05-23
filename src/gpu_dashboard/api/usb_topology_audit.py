"""HTTP handler for /api/usb-topology-audit (R&D #48.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_usb_topology_audit_status(ctx: dict) -> Response:
    from ..modules import usb_topology_audit
    return 200, usb_topology_audit.status(ctx.get("config"))
