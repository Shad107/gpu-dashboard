"""HTTP handler — R&D #88.2 suspend mode selector auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_suspend_mode_selector_audit_status(
        ctx: dict) -> Response:
    from ..modules import suspend_mode_selector_audit
    return 200, suspend_mode_selector_audit.status(
        ctx.get("config"))
