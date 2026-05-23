"""HTTP handler for /api/psi-pressure (R&D #32.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_psi_pressure_status(ctx: dict) -> Response:
    from ..modules import psi_pressure
    return 200, psi_pressure.status(ctx.get("config"))
