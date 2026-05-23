"""HTTP handler for /api/cgroup-memcap (R&D #32.5)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_cgroup_memcap_status(ctx: dict) -> Response:
    from ..modules import cgroup_memcap
    return 200, cgroup_memcap.status(ctx.get("config"))
