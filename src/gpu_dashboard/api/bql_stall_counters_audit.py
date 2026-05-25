"""HTTP handler — R&D #100.2 BQL stall counters auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_bql_stall_counters_audit_status(
        ctx: dict) -> Response:
    from ..modules import bql_stall_counters_audit
    return 200, bql_stall_counters_audit.status(
        ctx.get("config"))
