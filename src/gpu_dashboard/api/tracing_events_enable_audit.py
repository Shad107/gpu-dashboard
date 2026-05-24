"""HTTP handler for /api/tracing-events-enable-audit (R&D #72.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_tracing_events_enable_audit_status(ctx: dict) -> Response:
    from ..modules import tracing_events_enable_audit
    return 200, tracing_events_enable_audit.status(ctx.get("config"))
