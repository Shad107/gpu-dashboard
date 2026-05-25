"""HTTP handler — R&D #101.2 net qdisc default auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_net_qdisc_default_audit_status(
        ctx: dict) -> Response:
    from ..modules import net_qdisc_default_audit
    return 200, net_qdisc_default_audit.status(
        ctx.get("config"))
