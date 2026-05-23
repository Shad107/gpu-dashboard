"""HTTP handler for /api/ima-integrity-audit (R&D #53.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_ima_integrity_audit_status(ctx: dict) -> Response:
    from ..modules import ima_integrity_audit
    return 200, ima_integrity_audit.status(ctx.get("config"))
