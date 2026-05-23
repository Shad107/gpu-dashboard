"""HTTP handler for /api/proc-static-kernel-registry-audit (R&D #69.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_proc_static_kernel_registry_audit_status(ctx: dict) -> Response:
    from ..modules import proc_static_kernel_registry_audit
    return 200, proc_static_kernel_registry_audit.status(ctx.get("config"))
