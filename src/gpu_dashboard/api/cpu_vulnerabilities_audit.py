"""HTTP handler for /api/cpu-vulnerabilities-audit (R&D #53.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_cpu_vulnerabilities_audit_status(ctx: dict) -> Response:
    from ..modules import cpu_vulnerabilities_audit
    return 200, cpu_vulnerabilities_audit.status(ctx.get("config"))
