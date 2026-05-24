"""HTTP handler — R&D #83.2 clk_summary auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_clk_summary_audit_status(ctx: dict) -> Response:
    from ..modules import clk_summary_audit
    return 200, clk_summary_audit.status(ctx.get("config"))
