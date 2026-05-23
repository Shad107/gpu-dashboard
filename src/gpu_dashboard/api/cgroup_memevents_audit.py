"""HTTP handler for /api/cgroup-memevents-audit (R&D #50.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_cgroup_memevents_audit_status(ctx: dict) -> Response:
    from ..modules import cgroup_memevents_audit
    return 200, cgroup_memevents_audit.status(ctx.get("config"))
