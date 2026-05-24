"""HTTP handler — R&D #82.3 RCU expedited auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_rcu_expedited_audit_status(ctx: dict) -> Response:
    from ..modules import rcu_expedited_audit
    return 200, rcu_expedited_audit.status(ctx.get("config"))
