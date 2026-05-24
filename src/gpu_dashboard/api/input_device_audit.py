"""HTTP handler for /api/input-device-audit (R&D #76.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_input_device_audit_status(ctx: dict) -> Response:
    from ..modules import input_device_audit
    return 200, input_device_audit.status(ctx.get("config"))
