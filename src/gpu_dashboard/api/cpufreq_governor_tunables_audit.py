"""HTTP handler — R&D #90.3 cpufreq governor tunables auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_cpufreq_governor_tunables_audit_status(
        ctx: dict) -> Response:
    from ..modules import cpufreq_governor_tunables_audit
    return 200, cpufreq_governor_tunables_audit.status(
        ctx.get("config"))
