"""HTTP handler for /api/net-sysctl (R&D #35.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_net_sysctl_status(ctx: dict) -> Response:
    from ..modules import net_sysctl_audit
    return 200, net_sysctl_audit.status(ctx.get("config"))
