"""HTTP handler for /api/process-nice (R&D #19.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_process_nice_status(ctx: dict) -> Response:
    from ..modules import process_nice
    return 200, process_nice.status(ctx.get("config"))
