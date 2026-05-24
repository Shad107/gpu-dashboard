"""HTTP handler for /api/numa-hmat-access-audit (R&D #76.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_numa_hmat_access_audit_status(ctx: dict) -> Response:
    from ..modules import numa_hmat_access_audit
    return 200, numa_hmat_access_audit.status(ctx.get("config"))
