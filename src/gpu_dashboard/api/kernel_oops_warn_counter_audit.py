"""HTTP handler — R&D #103.1 kernel oops/warn counter auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_kernel_oops_warn_counter_audit_status(
        ctx: dict) -> Response:
    from ..modules import kernel_oops_warn_counter_audit
    return 200, kernel_oops_warn_counter_audit.status(
        ctx.get("config"))
