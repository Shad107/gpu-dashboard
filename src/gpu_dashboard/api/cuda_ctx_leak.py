"""HTTP handler for /api/cuda-ctx-leak (R&D #26.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_cuda_ctx_leak_status(ctx: dict) -> Response:
    from ..modules import cuda_ctx_leak
    return 200, cuda_ctx_leak.status(ctx.get("config"))
