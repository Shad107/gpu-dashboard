"""HTTP handler — R&D #90.1 resctrl L3/MBA auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_resctrl_audit_status(ctx: dict) -> Response:
    from ..modules import resctrl_audit
    return 200, resctrl_audit.status(ctx.get("config"))
