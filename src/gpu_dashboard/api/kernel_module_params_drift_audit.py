"""HTTP handler — R&D #84.3 kernel module params drift."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_kernel_module_params_drift_audit_status(ctx: dict) -> Response:
    from ..modules import kernel_module_params_drift_audit
    return 200, kernel_module_params_drift_audit.status(ctx.get("config"))
