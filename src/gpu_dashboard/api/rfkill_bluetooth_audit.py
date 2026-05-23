"""HTTP handler for /api/rfkill-bluetooth-audit (R&D #63.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_rfkill_bluetooth_audit_status(ctx: dict) -> Response:
    from ..modules import rfkill_bluetooth_audit
    return 200, rfkill_bluetooth_audit.status(ctx.get("config"))
