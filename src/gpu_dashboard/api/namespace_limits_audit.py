"""HTTP handler — R&D #89.2 namespace limits auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_namespace_limits_audit_status(
        ctx: dict) -> Response:
    from ..modules import namespace_limits_audit
    return 200, namespace_limits_audit.status(
        ctx.get("config"))
