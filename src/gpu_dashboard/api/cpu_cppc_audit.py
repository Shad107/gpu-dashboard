"""HTTP handler for /api/cpu-cppc-audit (R&D #77.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_cpu_cppc_audit_status(ctx: dict) -> Response:
    from ..modules import cpu_cppc_audit
    return 200, cpu_cppc_audit.status(ctx.get("config"))
