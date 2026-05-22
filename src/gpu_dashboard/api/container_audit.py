"""HTTP handler for /api/container-audit (R&D #20.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_container_audit_status(ctx: dict) -> Response:
    from ..modules import container_audit
    return 200, container_audit.status(ctx.get("config"))
