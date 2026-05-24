"""HTTP handler — R&D #84.2 loop device auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_loop_device_audit_status(ctx: dict) -> Response:
    from ..modules import loop_device_audit
    return 200, loop_device_audit.status(ctx.get("config"))
