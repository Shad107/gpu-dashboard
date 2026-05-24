"""HTTP handler — R&D #79.4 sched tunables auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_sched_tunables_audit_status(ctx: dict) -> Response:
    from ..modules import sched_tunables_audit
    return 200, sched_tunables_audit.status(ctx.get("config"))
