"""HTTP handler — R&D #108.2 overlay module params auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_overlay_module_params_audit_status(
        ctx: dict) -> Response:
    from ..modules import overlay_module_params_audit
    return 200, overlay_module_params_audit.status(
        ctx.get("config"))
