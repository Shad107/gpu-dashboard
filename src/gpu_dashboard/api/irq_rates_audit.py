"""HTTP handler for /api/irq-rates-audit (R&D #43.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_irq_rates_audit_status(ctx: dict) -> Response:
    from ..modules import irq_rates_audit
    return 200, irq_rates_audit.status(ctx.get("config"))
