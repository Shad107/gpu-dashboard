"""HTTP handler — R&D #104.3 firmware loader policy auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_firmware_loader_policy_audit_status(
        ctx: dict) -> Response:
    from ..modules import firmware_loader_policy_audit
    return 200, firmware_loader_policy_audit.status(
        ctx.get("config"))
