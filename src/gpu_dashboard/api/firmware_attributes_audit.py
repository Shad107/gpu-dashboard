"""HTTP handler for /api/firmware-attributes-audit (R&D #73.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_firmware_attributes_audit_status(ctx: dict) -> Response:
    from ..modules import firmware_attributes_audit
    return 200, firmware_attributes_audit.status(ctx.get("config"))
