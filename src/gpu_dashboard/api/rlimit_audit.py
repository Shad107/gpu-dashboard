"""HTTP handler for /api/rlimit-audit (R&D #29.8)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_rlimit_audit_status(ctx: dict) -> Response:
    from ..modules import rlimit_audit
    return 200, rlimit_audit.status(ctx.get("config"))
