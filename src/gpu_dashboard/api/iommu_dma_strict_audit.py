"""HTTP handler — R&D #92.1 IOMMU strict mode auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_iommu_dma_strict_audit_status(
        ctx: dict) -> Response:
    from ..modules import iommu_dma_strict_audit
    return 200, iommu_dma_strict_audit.status(
        ctx.get("config"))
