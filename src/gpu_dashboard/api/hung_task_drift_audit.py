"""HTTP handler — R&D #104.2 hung_task drift auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_hung_task_drift_audit_status(
        ctx: dict) -> Response:
    from ..modules import hung_task_drift_audit
    return 200, hung_task_drift_audit.status(
        ctx.get("config"))
