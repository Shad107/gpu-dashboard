"""HTTP handler for /api/cache-topology (R&D #37.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_cache_topology_status(ctx: dict) -> Response:
    from ..modules import cpu_cache_topology
    return 200, cpu_cache_topology.status(ctx.get("config"))
