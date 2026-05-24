"""HTTP handler — R&D #81.3 cgroup io.stat auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_cgroup_io_stat_audit_status(ctx: dict) -> Response:
    from ..modules import cgroup_io_stat_audit
    return 200, cgroup_io_stat_audit.status(ctx.get("config"))
