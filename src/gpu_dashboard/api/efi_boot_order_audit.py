"""HTTP handler for /api/efi-boot-order-audit (R&D #55.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_efi_boot_order_audit_status(ctx: dict) -> Response:
    from ..modules import efi_boot_order_audit
    return 200, efi_boot_order_audit.status(ctx.get("config"))
