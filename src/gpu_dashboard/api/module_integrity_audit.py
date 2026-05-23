"""HTTP handler for /api/module-integrity-audit (R&D #52.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_module_integrity_audit_status(ctx: dict) -> Response:
    from ..modules import module_integrity_audit
    return 200, module_integrity_audit.status(ctx.get("config"))
