"""HTTP handler for /api/ptp-clock-audit (R&D #63.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_ptp_clock_audit_status(ctx: dict) -> Response:
    from ..modules import ptp_clock_audit
    return 200, ptp_clock_audit.status(ctx.get("config"))
