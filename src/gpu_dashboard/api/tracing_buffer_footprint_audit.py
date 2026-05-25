"""HTTP handler — R&D #95.3 ftrace buffer footprint auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_tracing_buffer_footprint_audit_status(
        ctx: dict) -> Response:
    from ..modules import tracing_buffer_footprint_audit
    return 200, tracing_buffer_footprint_audit.status(
        ctx.get("config"))
