"""HTTP handler for /api/sched-audit (R&D #47.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_sched_audit_status(ctx: dict) -> Response:
    from ..modules import sched_audit
    return 200, sched_audit.status(ctx.get("config"))
