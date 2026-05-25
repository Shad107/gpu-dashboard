"""HTTP handler — R&D #93.2 cgroup v2 memory.peak auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_cgroup_v2_memory_peak_audit_status(
        ctx: dict) -> Response:
    from ..modules import cgroup_v2_memory_peak_audit
    return 200, cgroup_v2_memory_peak_audit.status(
        ctx.get("config"))
