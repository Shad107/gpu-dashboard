"""HTTP handler for /api/clocksource (R&D #33.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_clocksource_status(ctx: dict) -> Response:
    from ..modules import clocksource_audit
    return 200, clocksource_audit.status(ctx.get("config"))
