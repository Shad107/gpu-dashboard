"""HTTP handler — R&D #83.4 DRI debugfs auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_dri_debugfs_audit_status(ctx: dict) -> Response:
    from ..modules import dri_debugfs_audit
    return 200, dri_debugfs_audit.status(ctx.get("config"))
