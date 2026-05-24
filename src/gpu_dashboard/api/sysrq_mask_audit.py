"""HTTP handler — R&D #82.1 sysrq mask auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_sysrq_mask_audit_status(ctx: dict) -> Response:
    from ..modules import sysrq_mask_audit
    return 200, sysrq_mask_audit.status(ctx.get("config"))
