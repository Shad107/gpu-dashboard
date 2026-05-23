"""HTTP handler for /api/nf-conntrack-audit (R&D #45.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_nf_conntrack_audit_status(ctx: dict) -> Response:
    from ..modules import nf_conntrack_audit
    return 200, nf_conntrack_audit.status(ctx.get("config"))
