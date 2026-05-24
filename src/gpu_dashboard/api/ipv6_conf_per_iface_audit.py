"""HTTP handler for /api/ipv6-conf-per-iface-audit (R&D #76.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_ipv6_conf_per_iface_audit_status(ctx: dict) -> Response:
    from ..modules import ipv6_conf_per_iface_audit
    return 200, ipv6_conf_per_iface_audit.status(ctx.get("config"))
