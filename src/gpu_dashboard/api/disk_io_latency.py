"""HTTP handler for /api/disk-io-latency (R&D #44.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_disk_io_latency_status(ctx: dict) -> Response:
    from ..modules import disk_io_latency
    return 200, disk_io_latency.status(ctx.get("config"))
