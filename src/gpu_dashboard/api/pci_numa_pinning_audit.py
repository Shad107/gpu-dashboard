"""HTTP handler — R&D #109.4 PCI NUMA pinning auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_pci_numa_pinning_audit_status(
        ctx: dict) -> Response:
    from ..modules import pci_numa_pinning_audit
    return 200, pci_numa_pinning_audit.status(
        ctx.get("config"))
