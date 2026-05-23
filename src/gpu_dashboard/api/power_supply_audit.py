"""HTTP handler for /api/power-supply-audit (R&D #51.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_power_supply_audit_status(ctx: dict) -> Response:
    from ..modules import power_supply_audit
    return 200, power_supply_audit.status(ctx.get("config"))
