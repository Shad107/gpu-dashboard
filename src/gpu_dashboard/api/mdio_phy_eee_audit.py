"""HTTP handler — R&D #95.1 MDIO PHY + EEE auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_mdio_phy_eee_audit_status(ctx: dict) -> Response:
    from ..modules import mdio_phy_eee_audit
    return 200, mdio_phy_eee_audit.status(ctx.get("config"))
