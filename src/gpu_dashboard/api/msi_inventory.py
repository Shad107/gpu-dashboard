"""HTTP handler for /api/msi-inventory (R&D #30.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_msi_inventory_status(ctx: dict) -> Response:
    from ..modules import msi_inventory
    return 200, msi_inventory.status(ctx.get("config"))
