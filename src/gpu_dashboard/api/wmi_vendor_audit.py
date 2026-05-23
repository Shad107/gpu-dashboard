"""HTTP handler for /api/wmi-vendor-audit (R&D #49.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_wmi_vendor_audit_status(ctx: dict) -> Response:
    from ..modules import wmi_vendor_audit
    return 200, wmi_vendor_audit.status(ctx.get("config"))
