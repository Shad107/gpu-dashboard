"""HTTP handler for /api/oom-priority (R&D #31.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_oom_priority_status(ctx: dict) -> Response:
    from ..modules import oom_priority
    return 200, oom_priority.status(ctx.get("config"))
