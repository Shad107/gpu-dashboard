"""HTTP handler — R&D #107.3 vm dirty_bytes drift auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_vm_dirty_bytes_drift_audit_status(
        ctx: dict) -> Response:
    from ..modules import vm_dirty_bytes_drift_audit
    return 200, vm_dirty_bytes_drift_audit.status(
        ctx.get("config"))
