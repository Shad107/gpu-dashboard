"""HTTP handler for /api/hwmon-inventory (R&D #31.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_hwmon_inventory_status(ctx: dict) -> Response:
    from ..modules import hwmon_inventory
    return 200, hwmon_inventory.status(ctx.get("config"))
