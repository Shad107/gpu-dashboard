"""HTTP handler for /api/cgroup-cpuio (R&D #33.6)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_cgroup_cpuio_status(ctx: dict) -> Response:
    from ..modules import cgroup_cpuio
    return 200, cgroup_cpuio.status(ctx.get("config"))
