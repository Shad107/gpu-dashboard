"""HTTP handler — R&D #84.1 suspend stats auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_suspend_stats_audit_status(ctx: dict) -> Response:
    from ..modules import suspend_stats_audit
    return 200, suspend_stats_audit.status(ctx.get("config"))
