"""HTTP handler — R&D #91.1 cgroup pids controller auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_cgroup_pids_controller_audit_status(
        ctx: dict) -> Response:
    from ..modules import cgroup_pids_controller_audit
    return 200, cgroup_pids_controller_audit.status(
        ctx.get("config"))
