"""HTTP handler for /api/proc-sched (R&D #34.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_proc_sched_status(ctx: dict) -> Response:
    from ..modules import proc_sched
    return 200, proc_sched.status(ctx.get("config"))
