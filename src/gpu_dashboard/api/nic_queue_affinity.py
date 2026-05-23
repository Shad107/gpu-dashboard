"""HTTP handler for /api/nic-queue-affinity (R&D #40.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_nic_queue_affinity_status(ctx: dict) -> Response:
    from ..modules import nic_queue_affinity
    return 200, nic_queue_affinity.status(ctx.get("config"))
