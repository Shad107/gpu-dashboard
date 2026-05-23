"""HTTP handler for /api/driver-flavor (R&D #22.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_driver_flavor_status(ctx: dict) -> Response:
    from ..modules import driver_flavor
    return 200, driver_flavor.status(ctx.get("config"))
