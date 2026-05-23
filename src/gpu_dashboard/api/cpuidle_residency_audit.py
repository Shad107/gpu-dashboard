"""HTTP handler for /api/cpuidle-residency-audit (R&D #65.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_cpuidle_residency_audit_status(ctx: dict) -> Response:
    from ..modules import cpuidle_residency_audit
    return 200, cpuidle_residency_audit.status(ctx.get("config"))
