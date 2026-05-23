"""HTTP handler for /api/timer-list-audit (R&D #67.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_timer_list_audit_status(ctx: dict) -> Response:
    from ..modules import timer_list_audit
    return 200, timer_list_audit.status(ctx.get("config"))
