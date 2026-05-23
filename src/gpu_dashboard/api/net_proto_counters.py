"""HTTP handler for /api/net-proto-counters (R&D #44.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_net_proto_counters_status(ctx: dict) -> Response:
    from ..modules import net_proto_counters
    return 200, net_proto_counters.status(ctx.get("config"))
