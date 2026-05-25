"""HTTP handler — R&D #95.2 module refcnt auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_kernel_module_refcnt_audit_status(
        ctx: dict) -> Response:
    from ..modules import kernel_module_refcnt_audit
    return 200, kernel_module_refcnt_audit.status(
        ctx.get("config"))
