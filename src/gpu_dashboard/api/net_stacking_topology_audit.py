"""HTTP handler — R&D #78.2 net stacking topology audit."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_net_stacking_topology_audit_status(ctx: dict) -> Response:
    from ..modules import net_stacking_topology_audit
    return 200, net_stacking_topology_audit.status(ctx.get("config"))
