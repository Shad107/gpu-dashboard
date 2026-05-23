"""HTTP handler for /api/hw-watchdog (R&D #37.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_hw_watchdog_status(ctx: dict) -> Response:
    from ..modules import hw_watchdog
    return 200, hw_watchdog.status(ctx.get("config"))
