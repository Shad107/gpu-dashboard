"""HTTP handler for /api/dt-memmap-firmware-audit (R&D #68.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_dt_memmap_firmware_audit_status(ctx: dict) -> Response:
    from ..modules import dt_memmap_firmware_audit
    return 200, dt_memmap_firmware_audit.status(ctx.get("config"))
