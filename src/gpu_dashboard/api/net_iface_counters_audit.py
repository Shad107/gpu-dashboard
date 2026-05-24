"""HTTP handler for /api/net-iface-counters-audit (R&D #78.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_net_iface_counters_audit_status(ctx: dict) -> Response:
    from ..modules import net_iface_counters_audit
    return 200, net_iface_counters_audit.status(ctx.get("config"))
