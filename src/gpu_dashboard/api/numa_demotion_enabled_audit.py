"""HTTP handler — R&D #109.1 NUMA demotion auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_numa_demotion_enabled_audit_status(
        ctx: dict) -> Response:
    from ..modules import numa_demotion_enabled_audit
    return 200, numa_demotion_enabled_audit.status(
        ctx.get("config"))
