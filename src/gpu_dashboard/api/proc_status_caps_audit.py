"""HTTP handler — R&D #80.4 /proc/<pid>/status caps auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_proc_status_caps_audit_status(ctx: dict) -> Response:
    from ..modules import proc_status_caps_audit
    return 200, proc_status_caps_audit.status(ctx.get("config"))
