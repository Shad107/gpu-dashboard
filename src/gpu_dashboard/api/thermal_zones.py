"""HTTP handler for /api/thermal-zones (R&D #28.5)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_thermal_zones_status(ctx: dict) -> Response:
    from ..modules import thermal_zones
    return 200, thermal_zones.status(ctx.get("config"))
