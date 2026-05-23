"""HTTP handler for /api/virt-guest-detect-audit (R&D #60.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_virt_guest_detect_audit_status(ctx: dict) -> Response:
    from ..modules import virt_guest_detect_audit
    return 200, virt_guest_detect_audit.status(ctx.get("config"))
