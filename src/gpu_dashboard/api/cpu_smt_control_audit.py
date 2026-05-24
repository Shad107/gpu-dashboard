"""HTTP handler — R&D #87.3 CPU SMT control auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_cpu_smt_control_audit_status(ctx: dict) -> Response:
    from ..modules import cpu_smt_control_audit
    return 200, cpu_smt_control_audit.status(ctx.get("config"))
