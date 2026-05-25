"""HTTP handler — R&D #108.4 cgroup tree limits auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_cgroup_tree_limits_audit_status(
        ctx: dict) -> Response:
    from ..modules import cgroup_tree_limits_audit
    return 200, cgroup_tree_limits_audit.status(
        ctx.get("config"))
