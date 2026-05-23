"""HTTP handler for /api/sysvipc-audit (R&D #45.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_sysvipc_audit_status(ctx: dict) -> Response:
    from ..modules import sysvipc_audit
    return 200, sysvipc_audit.status(ctx.get("config"))
