"""HTTP handler for /api/proc-wchan (R&D #32.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_proc_wchan_status(ctx: dict) -> Response:
    from ..modules import proc_wchan
    return 200, proc_wchan.status(ctx.get("config"))
