"""HTTP handler for /api/cpu-rapl (R&D #27.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_cpu_rapl_status(ctx: dict) -> Response:
    from ..modules import cpu_rapl
    return 200, cpu_rapl.status(ctx.get("config"))
