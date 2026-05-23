"""HTTP handler for /api/ksm-audit (R&D #52.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_ksm_audit_status(ctx: dict) -> Response:
    from ..modules import ksm_audit
    return 200, ksm_audit.status(ctx.get("config"))
