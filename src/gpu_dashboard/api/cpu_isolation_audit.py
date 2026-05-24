"""HTTP handler for /api/cpu-isolation-audit (R&D #74.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_cpu_isolation_audit_status(ctx: dict) -> Response:
    from ..modules import cpu_isolation_audit
    return 200, cpu_isolation_audit.status(ctx.get("config"))
