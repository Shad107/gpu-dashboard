"""HTTP handler — R&D #107.4 NUMA balancing scan tuning auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_numa_balancing_scan_tuning_audit_status(
        ctx: dict) -> Response:
    from ..modules import numa_balancing_scan_tuning_audit
    return 200, numa_balancing_scan_tuning_audit.status(
        ctx.get("config"))
