"""HTTP handler for /api/numa-placement (R&D #35.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_numa_placement_status(ctx: dict) -> Response:
    from ..modules import numa_placement
    return 200, numa_placement.status(ctx.get("config"))
