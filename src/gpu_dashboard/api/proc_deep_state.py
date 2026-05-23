"""HTTP handler for /api/proc-deep-state (R&D #23.6)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_proc_deep_state_status(ctx: dict) -> Response:
    from ..modules import proc_deep_state
    return 200, proc_deep_state.status(ctx.get("config"))
