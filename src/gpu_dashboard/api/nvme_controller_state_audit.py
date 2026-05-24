"""HTTP handler — R&D #86.3 NVMe controller state auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_nvme_controller_state_audit_status(ctx: dict) -> Response:
    from ..modules import nvme_controller_state_audit
    return 200, nvme_controller_state_audit.status(ctx.get("config"))
