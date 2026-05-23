"""HTTP handler for /api/cpu-topology (R&D #31.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_cpu_topology_status(ctx: dict) -> Response:
    from ..modules import cpu_topology
    return 200, cpu_topology.status(ctx.get("config"))
