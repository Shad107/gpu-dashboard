"""HTTP handler for /api/damon-cma-audit (R&D #69.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_damon_cma_audit_status(ctx: dict) -> Response:
    from ..modules import damon_cma_audit
    return 200, damon_cma_audit.status(ctx.get("config"))
