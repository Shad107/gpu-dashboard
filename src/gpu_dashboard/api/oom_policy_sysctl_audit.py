"""HTTP handler — R&D #99.3 OOM policy sysctl auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_oom_policy_sysctl_audit_status(
        ctx: dict) -> Response:
    from ..modules import oom_policy_sysctl_audit
    return 200, oom_policy_sysctl_audit.status(
        ctx.get("config"))
