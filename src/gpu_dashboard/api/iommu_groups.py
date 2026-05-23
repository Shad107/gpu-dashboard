"""HTTP handler for /api/iommu-groups (R&D #30.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_iommu_groups_status(ctx: dict) -> Response:
    from ..modules import iommu_groups
    return 200, iommu_groups.status(ctx.get("config"))
