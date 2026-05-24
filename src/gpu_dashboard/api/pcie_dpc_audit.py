"""HTTP handler — R&D #90.4 PCIe DPC auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_pcie_dpc_audit_status(ctx: dict) -> Response:
    from ..modules import pcie_dpc_audit
    return 200, pcie_dpc_audit.status(ctx.get("config"))
