"""HTTP handler for /api/mps-health (R&D #19.6)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_mps_health_status(ctx: dict) -> Response:
    from ..modules import mps_health
    return 200, mps_health.status(ctx.get("config"))
