"""HTTP handler for /api/proc-io (R&D #33.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_proc_io_status(ctx: dict) -> Response:
    from ..modules import proc_io_accounting
    return 200, proc_io_accounting.status(ctx.get("config"))
