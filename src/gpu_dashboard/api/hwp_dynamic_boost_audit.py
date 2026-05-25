"""HTTP handler — R&D #104.1 HWP dynamic_boost auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_hwp_dynamic_boost_audit_status(
        ctx: dict) -> Response:
    from ..modules import hwp_dynamic_boost_audit
    return 200, hwp_dynamic_boost_audit.status(
        ctx.get("config"))
