"""HTTP handler — R&D #92.2 kernel lockup watchdog auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_kernel_lockup_watchdog_audit_status(
        ctx: dict) -> Response:
    from ..modules import kernel_lockup_watchdog_audit
    return 200, kernel_lockup_watchdog_audit.status(
        ctx.get("config"))
