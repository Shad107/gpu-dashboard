"""HTTP handler for /api/throttle-cause (R&D #19.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_throttle_cause_status(ctx: dict) -> Response:
    from ..modules import throttle_cause
    return 200, throttle_cause.status(ctx.get("config"))
