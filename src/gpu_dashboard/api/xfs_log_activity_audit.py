"""HTTP handler — R&D #110.2 XFS log activity auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_xfs_log_activity_audit_status(
        ctx: dict) -> Response:
    from ..modules import xfs_log_activity_audit
    return 200, xfs_log_activity_audit.status(
        ctx.get("config"))
