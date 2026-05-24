"""HTTP handler for /api/wmi-bus-audit (R&D #76.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_wmi_bus_audit_status(ctx: dict) -> Response:
    from ..modules import wmi_bus_audit
    return 200, wmi_bus_audit.status(ctx.get("config"))
