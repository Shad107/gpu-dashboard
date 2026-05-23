"""HTTP handler for /api/hwmon-sensors-audit (R&D #55.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_hwmon_sensors_audit_status(ctx: dict) -> Response:
    from ..modules import hwmon_sensors_audit
    return 200, hwmon_sensors_audit.status(ctx.get("config"))
