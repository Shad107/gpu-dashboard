"""HTTP handler — R&D #106.4 cpufreq setspeed drift auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_cpufreq_setspeed_drift_audit_status(
        ctx: dict) -> Response:
    from ..modules import cpufreq_setspeed_drift_audit
    return 200, cpufreq_setspeed_drift_audit.status(
        ctx.get("config"))
