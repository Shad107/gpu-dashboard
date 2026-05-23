"""HTTP handler for /api/nic-health (R&D #33.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_nic_health_status(ctx: dict) -> Response:
    from ..modules import nic_health
    return 200, nic_health.status(ctx.get("config"))
