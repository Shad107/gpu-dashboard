"""HTTP handler for /api/gpu-cpu-affinity (R&D #37.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_gpu_cpu_affinity_status(ctx: dict) -> Response:
    from ..modules import gpu_cpu_affinity
    return 200, gpu_cpu_affinity.status(ctx.get("config"))
