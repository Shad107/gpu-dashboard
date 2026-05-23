"""HTTP handler for /api/thermal-slowdown-kind (R&D #29.7)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_thermal_slowdown_kind_status(ctx: dict) -> Response:
    from ..modules import thermal_slowdown_kind
    return 200, thermal_slowdown_kind.status(ctx.get("config"))
