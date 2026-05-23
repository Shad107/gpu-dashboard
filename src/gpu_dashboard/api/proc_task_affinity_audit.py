"""HTTP handler for /api/proc-task-affinity-audit (R&D #62.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_proc_task_affinity_audit_status(ctx: dict) -> Response:
    from ..modules import proc_task_affinity_audit
    return 200, proc_task_affinity_audit.status(ctx.get("config"))
