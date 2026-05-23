"""HTTP handler for /api/iio-sensor-audit (R&D #50.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_iio_sensor_audit_status(ctx: dict) -> Response:
    from ..modules import iio_sensor_audit
    return 200, iio_sensor_audit.status(ctx.get("config"))
