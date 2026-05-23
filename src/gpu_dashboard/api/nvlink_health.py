"""HTTP handler for /api/nvlink-health (R&D #28.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_nvlink_health_status(ctx: dict) -> Response:
    from ..modules import nvlink_health
    return 200, nvlink_health.status(ctx.get("config"))
