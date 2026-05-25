"""HTTP handler — R&D #94.2 fs.aio + fanotify limits auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_fs_aio_fanotify_limits_audit_status(
        ctx: dict) -> Response:
    from ..modules import fs_aio_fanotify_limits_audit
    return 200, fs_aio_fanotify_limits_audit.status(
        ctx.get("config"))
