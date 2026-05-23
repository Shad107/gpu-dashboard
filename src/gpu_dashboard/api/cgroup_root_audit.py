"""HTTP handler for /api/cgroup-root-audit (R&D #58.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_cgroup_root_audit_status(ctx: dict) -> Response:
    from ..modules import cgroup_root_audit
    return 200, cgroup_root_audit.status(ctx.get("config"))
