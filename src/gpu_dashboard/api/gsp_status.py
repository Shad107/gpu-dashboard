"""HTTP handler for /api/gsp-status (R&D #21.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_gsp_status(ctx: dict) -> Response:
    from ..modules import gsp_status
    return 200, gsp_status.status(ctx.get("config"))
