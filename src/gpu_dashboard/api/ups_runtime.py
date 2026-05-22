"""HTTP handler for /api/ups-runtime (R&D #20.7)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_ups_runtime_status(ctx: dict) -> Response:
    from ..modules import ups_runtime
    return 200, ups_runtime.status(ctx.get("config"))
