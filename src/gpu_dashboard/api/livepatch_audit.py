"""HTTP handler for /api/livepatch-audit (R&D #57.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_livepatch_audit_status(ctx: dict) -> Response:
    from ..modules import livepatch_audit
    return 200, livepatch_audit.status(ctx.get("config"))
