"""HTTP handler for /api/devlink-smartnic-audit (R&D #64.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_devlink_smartnic_audit_status(ctx: dict) -> Response:
    from ..modules import devlink_smartnic_audit
    return 200, devlink_smartnic_audit.status(ctx.get("config"))
