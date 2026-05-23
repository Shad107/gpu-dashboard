"""HTTP handler for /api/typec-audit (R&D #51.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_typec_audit_status(ctx: dict) -> Response:
    from ..modules import typec_audit
    return 200, typec_audit.status(ctx.get("config"))
