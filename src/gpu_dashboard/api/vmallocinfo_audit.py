"""HTTP handler for /api/vmallocinfo-audit (R&D #67.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_vmallocinfo_audit_status(ctx: dict) -> Response:
    from ..modules import vmallocinfo_audit
    return 200, vmallocinfo_audit.status(ctx.get("config"))
