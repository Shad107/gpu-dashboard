"""HTTP handler for /api/proc-ns-mountinfo-audit (R&D #64.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_proc_ns_mountinfo_audit_status(ctx: dict) -> Response:
    from ..modules import proc_ns_mountinfo_audit
    return 200, proc_ns_mountinfo_audit.status(ctx.get("config"))
