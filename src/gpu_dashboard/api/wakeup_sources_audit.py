"""HTTP handler for /api/wakeup-sources-audit (R&D #56.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_wakeup_sources_audit_status(ctx: dict) -> Response:
    from ..modules import wakeup_sources_audit
    return 200, wakeup_sources_audit.status(ctx.get("config"))
