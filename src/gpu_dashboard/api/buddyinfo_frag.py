"""HTTP handler for /api/buddyinfo (R&D #34.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_buddyinfo_status(ctx: dict) -> Response:
    from ..modules import buddyinfo_frag
    return 200, buddyinfo_frag.status(ctx.get("config"))
