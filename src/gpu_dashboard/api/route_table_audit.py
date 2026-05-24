"""HTTP handler — R&D #79.2 route table auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_route_table_audit_status(ctx: dict) -> Response:
    from ..modules import route_table_audit
    return 200, route_table_audit.status(ctx.get("config"))
