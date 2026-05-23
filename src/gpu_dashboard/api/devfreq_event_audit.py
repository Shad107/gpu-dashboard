"""HTTP handler for /api/devfreq-event-audit (R&D #65.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_devfreq_event_audit_status(ctx: dict) -> Response:
    from ..modules import devfreq_event_audit
    return 200, devfreq_event_audit.status(ctx.get("config"))
