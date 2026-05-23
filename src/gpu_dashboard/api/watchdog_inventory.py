"""HTTP handler for /api/watchdog-inventory (R&D #44.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_watchdog_inventory_status(ctx: dict) -> Response:
    from ..modules import watchdog_inventory
    return 200, watchdog_inventory.status(ctx.get("config"))
