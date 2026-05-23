"""HTTP handler for /api/nic-ring-audit (R&D #43.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_nic_ring_audit_status(ctx: dict) -> Response:
    from ..modules import nic_ring_audit
    return 200, nic_ring_audit.status(ctx.get("config"))
