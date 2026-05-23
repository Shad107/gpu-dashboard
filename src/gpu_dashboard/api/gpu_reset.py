"""HTTP handler for /api/gpu-reset (R&D #22.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_gpu_reset_status(ctx: dict) -> Response:
    from ..modules import gpu_reset
    return 200, gpu_reset.status(ctx.get("config"))
