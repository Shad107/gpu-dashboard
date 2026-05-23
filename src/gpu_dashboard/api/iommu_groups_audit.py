"""HTTP handler for /api/iommu-groups-audit (R&D #59.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_iommu_groups_audit_status(ctx: dict) -> Response:
    from ..modules import iommu_groups_audit
    return 200, iommu_groups_audit.status(ctx.get("config"))
