"""HTTP handler for /api/cuda-advisor (R&D #18.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_cuda_advisor_status(ctx: dict) -> Response:
    from ..modules import cuda_advisor
    return 200, cuda_advisor.status(ctx.get("config"))
