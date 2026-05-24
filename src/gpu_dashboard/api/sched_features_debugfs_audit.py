"""HTTP handler — R&D #85.4 sched_features debugfs auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_sched_features_debugfs_audit_status(ctx: dict) -> Response:
    from ..modules import sched_features_debugfs_audit
    return 200, sched_features_debugfs_audit.status(ctx.get("config"))
