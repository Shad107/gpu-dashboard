"""HTTP handler — R&D #107.2 CAD + poweroff_cmd auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_sysrq_cad_poweroff_audit_status(
        ctx: dict) -> Response:
    from ..modules import sysrq_cad_poweroff_audit
    return 200, sysrq_cad_poweroff_audit.status(
        ctx.get("config"))
