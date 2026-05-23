"""HTTP handler for /api/cpu-vulns (R&D #37.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_cpu_vulns_status(ctx: dict) -> Response:
    from ..modules import cpu_vulns
    return 200, cpu_vulns.status(ctx.get("config"))
