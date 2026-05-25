"""HTTP handler — R&D #100.3 perf sampling limits auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_perf_sampling_limits_audit_status(
        ctx: dict) -> Response:
    from ..modules import perf_sampling_limits_audit
    return 200, perf_sampling_limits_audit.status(
        ctx.get("config"))
