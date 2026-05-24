"""HTTP handler — R&D #89.4 PCIe link drift auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_pcie_link_speed_drift_audit_status(
        ctx: dict) -> Response:
    from ..modules import pcie_link_speed_drift_audit
    return 200, pcie_link_speed_drift_audit.status(
        ctx.get("config"))
