"""HTTP handler — R&D #96.2 cgroup v2 cpuset partition auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_cpuset_v2_partition_audit_status(
        ctx: dict) -> Response:
    from ..modules import cpuset_v2_partition_audit
    return 200, cpuset_v2_partition_audit.status(
        ctx.get("config"))
