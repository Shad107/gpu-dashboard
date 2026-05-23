"""HTTP handler for /api/mem-temp-drift (R&D #24.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_mem_temp_drift_status(ctx: dict) -> Response:
    from ..modules import mem_temp_drift
    return 200, mem_temp_drift.status(ctx.get("config"))
