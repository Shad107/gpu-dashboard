"""HTTP handler for /api/pcie-rpm-audit (R&D #28.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_pcie_rpm_audit_status(ctx: dict) -> Response:
    from ..modules import pcie_rpm_audit
    return 200, pcie_rpm_audit.status(ctx.get("config"))
