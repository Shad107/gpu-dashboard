"""HTTP handler for /api/journal-audit (R&D #48.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_journal_audit_status(ctx: dict) -> Response:
    from ..modules import journal_audit
    return 200, journal_audit.status(ctx.get("config"))
