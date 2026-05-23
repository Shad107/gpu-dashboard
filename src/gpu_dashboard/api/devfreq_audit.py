"""HTTP handler for /api/devfreq-audit (R&D #62.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_devfreq_audit_status(ctx: dict) -> Response:
    from ..modules import devfreq_audit
    return 200, devfreq_audit.status(ctx.get("config"))
