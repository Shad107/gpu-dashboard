"""HTTP handler — R&D #97.2 ZFS ARC auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_zfs_arc_audit_status(ctx: dict) -> Response:
    from ..modules import zfs_arc_audit
    return 200, zfs_arc_audit.status(ctx.get("config"))
