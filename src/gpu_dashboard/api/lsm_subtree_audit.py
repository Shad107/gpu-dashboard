"""HTTP handler for /api/lsm-subtree-audit (R&D #75.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_lsm_subtree_audit_status(ctx: dict) -> Response:
    from ..modules import lsm_subtree_audit
    return 200, lsm_subtree_audit.status(ctx.get("config"))
