"""HTTP handler for /api/ksm-advisor (R&D #40.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_ksm_advisor_status(ctx: dict) -> Response:
    from ..modules import ksm_advisor
    return 200, ksm_advisor.status(ctx.get("config"))
