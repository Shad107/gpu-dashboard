"""HTTP handler — R&D #81.4 thermal trip drift auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_thermal_trip_drift_audit_status(ctx: dict) -> Response:
    from ..modules import thermal_trip_drift_audit
    return 200, thermal_trip_drift_audit.status(ctx.get("config"))
