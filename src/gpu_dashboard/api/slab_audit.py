"""HTTP handler for /api/slab-audit (R&D #44.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_slab_audit_status(ctx: dict) -> Response:
    from ..modules import slab_audit
    return 200, slab_audit.status(ctx.get("config"))
