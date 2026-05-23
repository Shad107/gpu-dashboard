"""HTTP handler for /api/rtc-clock-audit (R&D #49.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_rtc_clock_audit_status(ctx: dict) -> Response:
    from ..modules import rtc_clock_audit
    return 200, rtc_clock_audit.status(ctx.get("config"))
