"""HTTP handler for /api/regulator-audit (R&D #61.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_regulator_audit_status(ctx: dict) -> Response:
    from ..modules import regulator_audit
    return 200, regulator_audit.status(ctx.get("config"))
