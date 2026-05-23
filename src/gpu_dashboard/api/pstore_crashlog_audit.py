"""HTTP handler for /api/pstore-crashlog-audit (R&D #68.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_pstore_crashlog_audit_status(ctx: dict) -> Response:
    from ..modules import pstore_crashlog_audit
    return 200, pstore_crashlog_audit.status(ctx.get("config"))
