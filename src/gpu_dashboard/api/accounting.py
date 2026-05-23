"""HTTP handler for /api/accounting (R&D #24.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_accounting_status(ctx: dict) -> Response:
    from ..modules import accounting
    return 200, accounting.status(ctx.get("config"))
