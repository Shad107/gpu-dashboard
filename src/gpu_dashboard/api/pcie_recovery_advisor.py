"""HTTP handler — F4 PCIe Recovery Wizard."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_pcie_recovery_advisor_status(ctx: dict) -> Response:
    from ..modules import pcie_recovery_advisor
    return 200, pcie_recovery_advisor.status(ctx.get("config"))
