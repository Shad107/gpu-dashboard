"""HTTP handler — R&D #102.2 modprobe blacklist drift auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_modprobe_blacklist_drift_audit_status(
        ctx: dict) -> Response:
    from ..modules import modprobe_blacklist_drift_audit
    return 200, modprobe_blacklist_drift_audit.status(
        ctx.get("config"))
