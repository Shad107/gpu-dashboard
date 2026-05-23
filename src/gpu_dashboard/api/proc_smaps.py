"""HTTP handler for /api/proc-smaps (R&D #31.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_proc_smaps_status(ctx: dict) -> Response:
    from ..modules import proc_smaps
    return 200, proc_smaps.status(ctx.get("config"))
