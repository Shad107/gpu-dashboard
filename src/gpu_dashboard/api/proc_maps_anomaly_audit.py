"""HTTP handler — R&D #78.4 /proc/<pid>/maps anomaly scan."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_proc_maps_anomaly_audit_status(ctx: dict) -> Response:
    from ..modules import proc_maps_anomaly_audit
    return 200, proc_maps_anomaly_audit.status(ctx.get("config"))
