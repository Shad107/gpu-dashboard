"""HTTP handler for /api/fdinfo-kinds-audit (R&D #67.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_fdinfo_kinds_audit_status(ctx: dict) -> Response:
    from ..modules import fdinfo_kinds_audit
    return 200, fdinfo_kinds_audit.status(ctx.get("config"))
