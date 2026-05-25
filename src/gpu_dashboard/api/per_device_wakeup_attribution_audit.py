"""HTTP handler — R&D #95.4 per-device wakeup attribution."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_per_device_wakeup_attribution_audit_status(
        ctx: dict) -> Response:
    from ..modules import per_device_wakeup_attribution_audit
    return 200, per_device_wakeup_attribution_audit.status(
        ctx.get("config"))
