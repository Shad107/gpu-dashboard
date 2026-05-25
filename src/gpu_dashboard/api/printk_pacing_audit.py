"""HTTP handler — R&D #106.2 printk pacing auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_printk_pacing_audit_status(
        ctx: dict) -> Response:
    from ..modules import printk_pacing_audit
    return 200, printk_pacing_audit.status(
        ctx.get("config"))
