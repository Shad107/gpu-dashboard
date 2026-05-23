"""HTTP handler for /api/cmdline-audit (R&D #39.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_cmdline_audit_status(ctx: dict) -> Response:
    from ..modules import cmdline_audit
    return 200, cmdline_audit.status(ctx.get("config"))
