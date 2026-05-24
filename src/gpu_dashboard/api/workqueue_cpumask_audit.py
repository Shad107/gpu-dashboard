"""HTTP handler — R&D #86.4 workqueue cpumask auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_workqueue_cpumask_audit_status(ctx: dict) -> Response:
    from ..modules import workqueue_cpumask_audit
    return 200, workqueue_cpumask_audit.status(ctx.get("config"))
