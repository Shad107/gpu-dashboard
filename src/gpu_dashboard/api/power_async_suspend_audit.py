"""HTTP handler — R&D #105.3 power-async suspend auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_power_async_suspend_audit_status(
        ctx: dict) -> Response:
    from ..modules import power_async_suspend_audit
    return 200, power_async_suspend_audit.status(
        ctx.get("config"))
