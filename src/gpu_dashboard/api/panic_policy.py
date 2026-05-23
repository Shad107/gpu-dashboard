"""HTTP handler for /api/panic-policy (R&D #41.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_panic_policy_status(ctx: dict) -> Response:
    from ..modules import panic_policy
    return 200, panic_policy.status(ctx.get("config"))
