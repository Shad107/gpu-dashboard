"""HTTP handler for /api/cooling-devices (R&D #42.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_cooling_devices_status(ctx: dict) -> Response:
    from ..modules import cooling_devices
    return 200, cooling_devices.status(ctx.get("config"))
