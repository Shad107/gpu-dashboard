"""HTTP handler — R&D #87.2 proc/locks contention auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_proc_locks_contention_audit_status(ctx: dict) -> Response:
    from ..modules import proc_locks_contention_audit
    return 200, proc_locks_contention_audit.status(ctx.get("config"))
