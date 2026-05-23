"""HTTP handler for /api/clock-gap (R&D #27.7)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_clock_gap_status(ctx: dict) -> Response:
    from ..modules import clock_gap
    return 200, clock_gap.status(ctx.get("config"))
