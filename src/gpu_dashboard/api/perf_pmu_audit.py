"""HTTP handler for /api/perf-pmu-audit (R&D #51.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_perf_pmu_audit_status(ctx: dict) -> Response:
    from ..modules import perf_pmu_audit
    return 200, perf_pmu_audit.status(ctx.get("config"))
