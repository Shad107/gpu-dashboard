"""HTTP handler for /api/pid-rlimits-audit (R&D #59.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_pid_rlimits_audit_status(ctx: dict) -> Response:
    from ..modules import pid_rlimits_audit
    return 200, pid_rlimits_audit.status(ctx.get("config"))
