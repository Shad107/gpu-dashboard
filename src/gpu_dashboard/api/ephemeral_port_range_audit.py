"""HTTP handler — R&D #103.2 ephemeral port range auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_ephemeral_port_range_audit_status(
        ctx: dict) -> Response:
    from ..modules import ephemeral_port_range_audit
    return 200, ephemeral_port_range_audit.status(
        ctx.get("config"))
