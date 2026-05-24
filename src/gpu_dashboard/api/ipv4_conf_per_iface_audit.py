"""HTTP handler for /api/ipv4-conf-per-iface-audit (R&D #75.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_ipv4_conf_per_iface_audit_status(ctx: dict) -> Response:
    from ..modules import ipv4_conf_per_iface_audit
    return 200, ipv4_conf_per_iface_audit.status(ctx.get("config"))
