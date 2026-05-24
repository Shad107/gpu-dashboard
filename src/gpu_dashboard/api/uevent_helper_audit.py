"""HTTP handler for /api/uevent-helper-audit (R&D #72.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_uevent_helper_audit_status(ctx: dict) -> Response:
    from ..modules import uevent_helper_audit
    return 200, uevent_helper_audit.status(ctx.get("config"))
