"""HTTP handler — R&D #88.4 timer_migration × nohz drift auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_timer_migration_nohz_drift_audit_status(
        ctx: dict) -> Response:
    from ..modules import timer_migration_nohz_drift_audit
    return 200, timer_migration_nohz_drift_audit.status(
        ctx.get("config"))
