"""HTTP handler for /api/cpu-microcode (R&D #36.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_cpu_microcode_status(ctx: dict) -> Response:
    from ..modules import cpu_microcode
    return 200, cpu_microcode.status(ctx.get("config"))
