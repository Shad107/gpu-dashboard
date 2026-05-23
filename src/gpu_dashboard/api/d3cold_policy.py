"""HTTP handler for /api/d3cold-policy (R&D #29.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_d3cold_policy_status(ctx: dict) -> Response:
    from ..modules import d3cold_policy
    return 200, d3cold_policy.status(ctx.get("config"))
