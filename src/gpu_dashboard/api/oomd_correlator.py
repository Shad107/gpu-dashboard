"""HTTP handler for /api/oomd (R&D #34.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_oomd_status(ctx: dict) -> Response:
    from ..modules import oomd_correlator
    return 200, oomd_correlator.status(ctx.get("config"))
