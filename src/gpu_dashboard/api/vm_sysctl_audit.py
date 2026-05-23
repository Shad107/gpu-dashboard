"""HTTP handler for /api/vm-sysctl (R&D #32.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_vm_sysctl_status(ctx: dict) -> Response:
    from ..modules import vm_sysctl_audit
    return 200, vm_sysctl_audit.status(ctx.get("config"))
