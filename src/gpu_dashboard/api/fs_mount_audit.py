"""HTTP handler for /api/fs-mount-audit (R&D #23.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_fs_mount_audit_status(ctx: dict) -> Response:
    from ..modules import fs_mount_audit
    return 200, fs_mount_audit.status(ctx.get("config"))
