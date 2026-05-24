"""HTTP handler for /api/cpu-thermal-throttle-counters-audit (R&D #77.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_cpu_thermal_throttle_counters_audit_status(ctx: dict) -> Response:
    from ..modules import cpu_thermal_throttle_counters_audit
    return 200, cpu_thermal_throttle_counters_audit.status(ctx.get("config"))
