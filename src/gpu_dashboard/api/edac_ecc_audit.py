"""HTTP handler for /api/edac-ecc-audit (R&D #55.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_edac_ecc_audit_status(ctx: dict) -> Response:
    from ..modules import edac_ecc_audit
    return 200, edac_ecc_audit.status(ctx.get("config"))
