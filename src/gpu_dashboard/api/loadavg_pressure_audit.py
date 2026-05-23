"""HTTP handler for /api/loadavg-pressure-audit (R&D #57.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_loadavg_pressure_audit_status(ctx: dict) -> Response:
    from ..modules import loadavg_pressure_audit
    return 200, loadavg_pressure_audit.status(ctx.get("config"))
