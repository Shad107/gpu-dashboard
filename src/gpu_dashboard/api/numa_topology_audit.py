"""HTTP handler for /api/numa-topology-audit (R&D #55.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_numa_topology_audit_status(ctx: dict) -> Response:
    from ..modules import numa_topology_audit
    return 200, numa_topology_audit.status(ctx.get("config"))
