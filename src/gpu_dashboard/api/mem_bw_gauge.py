"""HTTP handler for /api/mem-bw-gauge (R&D #26.8)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_mem_bw_gauge_status(ctx: dict) -> Response:
    from ..modules import mem_bw_gauge
    return 200, mem_bw_gauge.status(ctx.get("config"))
