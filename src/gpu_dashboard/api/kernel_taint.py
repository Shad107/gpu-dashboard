"""HTTP handler for /api/kernel-taint (R&D #36.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_kernel_taint_status(ctx: dict) -> Response:
    from ..modules import kernel_taint
    return 200, kernel_taint.status(ctx.get("config"))
