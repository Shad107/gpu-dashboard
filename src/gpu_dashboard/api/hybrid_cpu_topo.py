"""HTTP handler for /api/hybrid-cpu-topo (R&D #42.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_hybrid_cpu_topo_status(ctx: dict) -> Response:
    from ..modules import hybrid_cpu_topo
    return 200, hybrid_cpu_topo.status(ctx.get("config"))
