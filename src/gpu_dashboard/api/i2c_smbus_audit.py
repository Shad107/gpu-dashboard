"""HTTP handler for /api/i2c-smbus-audit (R&D #52.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_i2c_smbus_audit_status(ctx: dict) -> Response:
    from ..modules import i2c_smbus_audit
    return 200, i2c_smbus_audit.status(ctx.get("config"))
