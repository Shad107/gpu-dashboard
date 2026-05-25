"""HTTP handler — R&D #99.1 umwait control auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_umwait_control_audit_status(
        ctx: dict) -> Response:
    from ..modules import umwait_control_audit
    return 200, umwait_control_audit.status(
        ctx.get("config"))
