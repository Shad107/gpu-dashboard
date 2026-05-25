"""HTTP handler — R&D #96.3 tracing instances auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_tracing_instances_audit_status(
        ctx: dict) -> Response:
    from ..modules import tracing_instances_audit
    return 200, tracing_instances_audit.status(
        ctx.get("config"))
