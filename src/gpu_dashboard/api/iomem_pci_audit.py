"""HTTP handler for /api/iomem-pci-audit (R&D #51.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_iomem_pci_audit_status(ctx: dict) -> Response:
    from ..modules import iomem_pci_audit
    return 200, iomem_pci_audit.status(ctx.get("config"))
