"""HTTP handler — R&D #88.3 IOMMU reserved regions auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_iommu_reserved_regions_audit_status(
        ctx: dict) -> Response:
    from ..modules import iommu_reserved_regions_audit
    return 200, iommu_reserved_regions_audit.status(
        ctx.get("config"))
