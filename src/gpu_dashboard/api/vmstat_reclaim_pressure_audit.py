"""HTTP handler — R&D #91.4 vmstat reclaim pressure auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_vmstat_reclaim_pressure_audit_status(
        ctx: dict) -> Response:
    from ..modules import vmstat_reclaim_pressure_audit
    return 200, vmstat_reclaim_pressure_audit.status(
        ctx.get("config"))
