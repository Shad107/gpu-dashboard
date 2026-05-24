"""HTTP handler for /api/pci-sriov-posture-audit (R&D #77.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_pci_sriov_posture_audit_status(ctx: dict) -> Response:
    from ..modules import pci_sriov_posture_audit
    return 200, pci_sriov_posture_audit.status(ctx.get("config"))
