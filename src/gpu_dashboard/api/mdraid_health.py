"""HTTP handler for /api/mdraid-health (R&D #45.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_mdraid_health_status(ctx: dict) -> Response:
    from ..modules import mdraid_health
    return 200, mdraid_health.status(ctx.get("config"))
