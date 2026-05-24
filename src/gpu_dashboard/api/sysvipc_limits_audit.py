"""HTTP handler — R&D #89.3 SysV IPC limits auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_sysvipc_limits_audit_status(
        ctx: dict) -> Response:
    from ..modules import sysvipc_limits_audit
    return 200, sysvipc_limits_audit.status(ctx.get("config"))
