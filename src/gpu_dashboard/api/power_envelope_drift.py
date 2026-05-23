"""HTTP handler for /api/power-envelope-drift (R&D #27.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_power_envelope_drift_status(ctx: dict) -> Response:
    from ..modules import power_envelope_drift
    return 200, power_envelope_drift.status(ctx.get("config"))
