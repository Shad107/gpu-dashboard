"""HTTP handler for /api/trim-audit (R&D #25.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_trim_audit_status(ctx: dict) -> Response:
    from ..modules import trim_audit
    return 200, trim_audit.status(ctx.get("config"))
