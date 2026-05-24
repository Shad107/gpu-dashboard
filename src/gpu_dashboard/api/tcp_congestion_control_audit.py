"""HTTP handler — R&D #89.1 TCP CC selector auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_tcp_congestion_control_audit_status(
        ctx: dict) -> Response:
    from ..modules import tcp_congestion_control_audit
    return 200, tcp_congestion_control_audit.status(
        ctx.get("config"))
