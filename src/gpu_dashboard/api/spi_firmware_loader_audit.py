"""HTTP handler for /api/spi-firmware-loader-audit (R&D #66.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_spi_firmware_loader_audit_status(ctx: dict) -> Response:
    from ..modules import spi_firmware_loader_audit
    return 200, spi_firmware_loader_audit.status(ctx.get("config"))
