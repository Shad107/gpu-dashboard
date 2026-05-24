"""HTTP handler — R&D #79.1 softnet_stat auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_softnet_stat_audit_status(ctx: dict) -> Response:
    from ..modules import softnet_stat_audit
    return 200, softnet_stat_audit.status(ctx.get("config"))
