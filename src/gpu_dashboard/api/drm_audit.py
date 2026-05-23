"""HTTP handler for /api/drm-audit (R&D #50.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_drm_audit_status(ctx: dict) -> Response:
    from ..modules import drm_audit
    return 200, drm_audit.status(ctx.get("config"))
