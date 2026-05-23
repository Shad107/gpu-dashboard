"""HTTP handler for /api/pagetypeinfo-audit (R&D #57.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_pagetypeinfo_audit_status(ctx: dict) -> Response:
    from ..modules import pagetypeinfo_audit
    return 200, pagetypeinfo_audit.status(ctx.get("config"))
