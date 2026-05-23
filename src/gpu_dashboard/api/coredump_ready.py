"""HTTP handler for /api/coredump (R&D #39.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_coredump_ready_status(ctx: dict) -> Response:
    from ..modules import coredump_ready
    return 200, coredump_ready.status(ctx.get("config"))
