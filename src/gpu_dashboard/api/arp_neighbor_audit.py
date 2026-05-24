"""HTTP handler — R&D #80.1 ARP neighbor auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_arp_neighbor_audit_status(ctx: dict) -> Response:
    from ..modules import arp_neighbor_audit
    return 200, arp_neighbor_audit.status(ctx.get("config"))
