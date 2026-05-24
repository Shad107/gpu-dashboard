"""HTTP handler — R&D #79.3 fb/vtconsole auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_fb_vtconsole_audit_status(ctx: dict) -> Response:
    from ..modules import fb_vtconsole_audit
    return 200, fb_vtconsole_audit.status(ctx.get("config"))
