"""HTTP handler for /api/lru-gen-mglru-audit (R&D #68.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_lru_gen_mglru_audit_status(ctx: dict) -> Response:
    from ..modules import lru_gen_mglru_audit
    return 200, lru_gen_mglru_audit.status(ctx.get("config"))
