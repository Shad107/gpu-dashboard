"""HTTP handler — R&D #101.4 KSM advisor + smart_scan auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_ksm_advisor_audit_status(
        ctx: dict) -> Response:
    from ..modules import ksm_advisor_audit
    return 200, ksm_advisor_audit.status(
        ctx.get("config"))
