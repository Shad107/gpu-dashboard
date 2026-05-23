"""HTTP handler for /api/cpufreq-residency-audit (R&D #65.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_cpufreq_residency_audit_status(ctx: dict) -> Response:
    from ..modules import cpufreq_residency_audit
    return 200, cpufreq_residency_audit.status(ctx.get("config"))
