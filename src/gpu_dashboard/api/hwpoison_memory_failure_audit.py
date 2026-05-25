"""HTTP handler — R&D #94.1 hwpoison memory failure auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_hwpoison_memory_failure_audit_status(
        ctx: dict) -> Response:
    from ..modules import hwpoison_memory_failure_audit
    return 200, hwpoison_memory_failure_audit.status(
        ctx.get("config"))
