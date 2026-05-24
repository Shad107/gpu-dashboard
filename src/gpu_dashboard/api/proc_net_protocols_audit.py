"""HTTP handler — R&D #90.2 /proc/net protocols auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_proc_net_protocols_audit_status(
        ctx: dict) -> Response:
    from ..modules import proc_net_protocols_audit
    return 200, proc_net_protocols_audit.status(
        ctx.get("config"))
