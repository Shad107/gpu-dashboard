"""HTTP handler for /api/swap-tunables-audit (R&D #54.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_swap_tunables_audit_status(ctx: dict) -> Response:
    from ..modules import swap_tunables_audit
    return 200, swap_tunables_audit.status(ctx.get("config"))
