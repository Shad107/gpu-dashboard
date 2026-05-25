"""HTTP handler — R&D #107.1 vm numa policy auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_vm_numa_policy_audit_status(
        ctx: dict) -> Response:
    from ..modules import vm_numa_policy_audit
    return 200, vm_numa_policy_audit.status(
        ctx.get("config"))
