"""HTTP handler for /api/process-id-limits-audit (R&D #73.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_process_id_limits_audit_status(ctx: dict) -> Response:
    from ..modules import process_id_limits_audit
    return 200, process_id_limits_audit.status(ctx.get("config"))
