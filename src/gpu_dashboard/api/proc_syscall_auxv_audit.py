"""HTTP handler for /api/proc-syscall-auxv-audit (R&D #66.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_proc_syscall_auxv_audit_status(ctx: dict) -> Response:
    from ..modules import proc_syscall_auxv_audit
    return 200, proc_syscall_auxv_audit.status(ctx.get("config"))
