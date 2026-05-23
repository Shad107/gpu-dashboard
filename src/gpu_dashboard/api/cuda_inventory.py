"""HTTP handler for /api/cuda-inventory (R&D #22.5)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_cuda_inventory_status(ctx: dict) -> Response:
    from ..modules import cuda_inventory
    return 200, cuda_inventory.status(ctx.get("config"))
