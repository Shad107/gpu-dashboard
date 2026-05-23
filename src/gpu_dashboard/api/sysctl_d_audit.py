"""HTTP handler for /api/sysctl-d-audit (R&D #39.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_sysctl_d_audit_status(ctx: dict) -> Response:
    from ..modules import sysctl_d_audit
    return 200, sysctl_d_audit.status(ctx.get("config"))
