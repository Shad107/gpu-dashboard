"""HTTP handler for /api/vfs-limits-audit (R&D #46.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_vfs_limits_audit_status(ctx: dict) -> Response:
    from ..modules import vfs_limits_audit
    return 200, vfs_limits_audit.status(ctx.get("config"))
