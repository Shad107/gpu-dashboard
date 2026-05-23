"""HTTP handler for /api/cpuidle (R&D #36.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_cpuidle_status(ctx: dict) -> Response:
    from ..modules import cpuidle_audit
    return 200, cpuidle_audit.status(ctx.get("config"))
