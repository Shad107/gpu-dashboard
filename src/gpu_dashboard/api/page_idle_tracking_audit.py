"""HTTP handler for /api/page-idle-tracking-audit (R&D #71.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_page_idle_tracking_audit_status(ctx: dict) -> Response:
    from ..modules import page_idle_tracking_audit
    return 200, page_idle_tracking_audit.status(ctx.get("config"))
