"""HTTP handler — R&D #99.2 split-lock detect auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_split_lock_detect_audit_status(
        ctx: dict) -> Response:
    from ..modules import split_lock_detect_audit
    return 200, split_lock_detect_audit.status(
        ctx.get("config"))
