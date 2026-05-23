"""HTTP handler for /api/host-class (R&D #39.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_host_class_status(ctx: dict) -> Response:
    from ..modules import host_class
    return 200, host_class.status(ctx.get("config"))
