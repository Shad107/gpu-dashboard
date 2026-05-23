"""HTTP handler for /api/efi-runtime-map-audit (R&D #65.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_efi_runtime_map_audit_status(ctx: dict) -> Response:
    from ..modules import efi_runtime_map_audit
    return 200, efi_runtime_map_audit.status(ctx.get("config"))
