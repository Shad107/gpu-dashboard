"""HTTP handler — R&D #85.1 dynamic_debug auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_dynamic_debug_audit_status(ctx: dict) -> Response:
    from ..modules import dynamic_debug_audit
    return 200, dynamic_debug_audit.status(ctx.get("config"))
