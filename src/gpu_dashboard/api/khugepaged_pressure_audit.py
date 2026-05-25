"""HTTP handler — R&D #92.3 khugepaged pressure auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_khugepaged_pressure_audit_status(
        ctx: dict) -> Response:
    from ..modules import khugepaged_pressure_audit
    return 200, khugepaged_pressure_audit.status(
        ctx.get("config"))
