"""HTTP handler for /api/sock-pool-audit (R&D #50.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_sock_pool_audit_status(ctx: dict) -> Response:
    from ..modules import sock_pool_audit
    return 200, sock_pool_audit.status(ctx.get("config"))
