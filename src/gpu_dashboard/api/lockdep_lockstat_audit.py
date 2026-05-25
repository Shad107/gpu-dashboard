"""HTTP handler — R&D #94.4 lockdep / lock_stat auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_lockdep_lockstat_audit_status(
        ctx: dict) -> Response:
    from ..modules import lockdep_lockstat_audit
    return 200, lockdep_lockstat_audit.status(
        ctx.get("config"))
