"""HTTP handler for /api/batch-advisor (R&D #23.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_batch_advisor_status(ctx: dict) -> Response:
    from ..modules import batch_advisor
    return 200, batch_advisor.status(ctx.get("config"))
