"""HTTP handler for /api/ftrace-audit (R&D #48.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_ftrace_audit_status(ctx: dict) -> Response:
    from ..modules import ftrace_audit
    return 200, ftrace_audit.status(ctx.get("config"))
