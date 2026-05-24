"""HTTP handler — R&D #85.3 unix socket inventory auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_unix_socket_inventory_audit_status(ctx: dict) -> Response:
    from ..modules import unix_socket_inventory_audit
    return 200, unix_socket_inventory_audit.status(ctx.get("config"))
