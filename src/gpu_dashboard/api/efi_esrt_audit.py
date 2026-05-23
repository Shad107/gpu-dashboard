"""HTTP handler for /api/efi-esrt-audit (R&D #67.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_efi_esrt_audit_status(ctx: dict) -> Response:
    from ..modules import efi_esrt_audit
    return 200, efi_esrt_audit.status(ctx.get("config"))
