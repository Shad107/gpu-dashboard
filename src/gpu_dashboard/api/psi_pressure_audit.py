"""HTTP handler for /api/psi-pressure-audit (R&D #53.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_psi_pressure_audit_status(ctx: dict) -> Response:
    from ..modules import psi_pressure_audit
    return 200, psi_pressure_audit.status(ctx.get("config"))
