"""HTTP handler — R&D #97.3 cgroup delegate auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_cgroup_delegate_audit_status(
        ctx: dict) -> Response:
    from ..modules import cgroup_delegate_audit
    return 200, cgroup_delegate_audit.status(
        ctx.get("config"))
