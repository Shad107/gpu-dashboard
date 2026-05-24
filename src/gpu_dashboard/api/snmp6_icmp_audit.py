"""HTTP handler — R&D #80.2 SNMP6 ICMP auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_snmp6_icmp_audit_status(ctx: dict) -> Response:
    from ..modules import snmp6_icmp_audit
    return 200, snmp6_icmp_audit.status(ctx.get("config"))
