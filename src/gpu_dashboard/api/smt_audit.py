"""HTTP handler for /api/smt-audit (R&D #35.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_smt_audit_status(ctx: dict) -> Response:
    from ..modules import smt_audit
    return 200, smt_audit.status(ctx.get("config"))
