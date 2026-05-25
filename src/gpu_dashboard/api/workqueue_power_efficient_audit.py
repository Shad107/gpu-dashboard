"""HTTP handler — R&D #100.1 workqueue power_efficient auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_workqueue_power_efficient_audit_status(
        ctx: dict) -> Response:
    from ..modules import workqueue_power_efficient_audit
    return 200, workqueue_power_efficient_audit.status(
        ctx.get("config"))
