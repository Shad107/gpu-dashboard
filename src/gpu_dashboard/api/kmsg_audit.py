"""HTTP handler for /api/kmsg-audit (R&D #49.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_kmsg_audit_status(ctx: dict) -> Response:
    from ..modules import kmsg_audit
    return 200, kmsg_audit.status(ctx.get("config"))
