"""HTTP handler — R&D #98.1 PSI irq + cpu.full auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_psi_irq_full_audit_status(
        ctx: dict) -> Response:
    from ..modules import psi_irq_full_audit
    return 200, psi_irq_full_audit.status(
        ctx.get("config"))
