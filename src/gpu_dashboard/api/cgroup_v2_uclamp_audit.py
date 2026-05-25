"""HTTP handler — R&D #103.4 cgroup v2 uclamp/zswap auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_cgroup_v2_uclamp_audit_status(
        ctx: dict) -> Response:
    from ..modules import cgroup_v2_uclamp_audit
    return 200, cgroup_v2_uclamp_audit.status(
        ctx.get("config"))
