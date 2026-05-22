"""HTTP handler for /api/cuda-matrix (R&D #18.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_cuda_matrix_status(ctx: dict) -> Response:
    from ..modules import cuda_matrix
    return 200, cuda_matrix.status(ctx.get("config"))
