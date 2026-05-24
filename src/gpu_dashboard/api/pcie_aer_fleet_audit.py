"""HTTP handler for /api/pcie-aer-fleet-audit (R&D #77.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_pcie_aer_fleet_audit_status(ctx: dict) -> Response:
    from ..modules import pcie_aer_fleet_audit
    return 200, pcie_aer_fleet_audit.status(ctx.get("config"))
