"""HTTP handler for /api/cpu-epb (R&D #42.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_cpu_epb_status(ctx: dict) -> Response:
    from ..modules import cpu_epb
    return 200, cpu_epb.status(ctx.get("config"))
