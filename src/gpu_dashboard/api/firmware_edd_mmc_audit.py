"""HTTP handler for /api/firmware-edd-mmc-audit (R&D #64.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_firmware_edd_mmc_audit_status(ctx: dict) -> Response:
    from ..modules import firmware_edd_mmc_audit
    return 200, firmware_edd_mmc_audit.status(ctx.get("config"))
