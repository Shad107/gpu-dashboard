"""HTTP handler — R&D #99.4 rseq kernel posture auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_rseq_kernel_audit_status(
        ctx: dict) -> Response:
    from ..modules import rseq_kernel_audit
    return 200, rseq_kernel_audit.status(
        ctx.get("config"))
