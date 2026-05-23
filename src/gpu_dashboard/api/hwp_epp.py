"""HTTP handler for /api/hwp-epp (R&D #36.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_hwp_epp_status(ctx: dict) -> Response:
    from ..modules import hwp_epp
    return 200, hwp_epp.status(ctx.get("config"))
