"""HTTP handler for /api/tpm-audit (R&D #49.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_tpm_audit_status(ctx: dict) -> Response:
    from ..modules import tpm_audit
    return 200, tpm_audit.status(ctx.get("config"))
