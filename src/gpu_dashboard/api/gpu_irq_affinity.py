"""HTTP handler for /api/gpu-irq-affinity (R&D #38.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_gpu_irq_affinity_status(ctx: dict) -> Response:
    from ..modules import gpu_irq_affinity
    return 200, gpu_irq_affinity.status(ctx.get("config"))
