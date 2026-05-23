"""HTTP handler for /api/inotify-audit (R&D #41.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_inotify_audit_status(ctx: dict) -> Response:
    from ..modules import inotify_audit
    return 200, inotify_audit.status(ctx.get("config"))
