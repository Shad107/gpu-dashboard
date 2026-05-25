"""HTTP handler — R&D #97.4 PCI D3cold runtime PM auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_pci_d3cold_runtime_audit_status(
        ctx: dict) -> Response:
    from ..modules import pci_d3cold_runtime_audit
    return 200, pci_d3cold_runtime_audit.status(
        ctx.get("config"))
