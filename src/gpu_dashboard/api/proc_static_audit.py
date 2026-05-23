"""HTTP handler for /api/proc-static-audit (R&D #26.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_proc_static_audit_status(ctx: dict) -> Response:
    from ..modules import proc_static_audit
    return 200, proc_static_audit.status(ctx.get("config"))
