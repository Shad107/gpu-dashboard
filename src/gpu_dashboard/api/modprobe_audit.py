"""HTTP handler for /api/modprobe-audit (R&D #38.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_modprobe_audit_status(ctx: dict) -> Response:
    from ..modules import modprobe_audit
    return 200, modprobe_audit.status(ctx.get("config"))
