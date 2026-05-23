"""HTTP handler for /api/cpu-boost (R&D #35.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_cpu_boost_status(ctx: dict) -> Response:
    from ..modules import cpu_boost
    return 200, cpu_boost.status(ctx.get("config"))
