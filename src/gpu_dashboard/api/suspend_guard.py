"""HTTP handler for /api/suspend-guard (R&D #20.5)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_suspend_guard_status(ctx: dict) -> Response:
    from ..modules import suspend_guard
    return 200, suspend_guard.status(ctx.get("config"))
