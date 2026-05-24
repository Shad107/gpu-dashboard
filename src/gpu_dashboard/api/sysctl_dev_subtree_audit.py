"""HTTP handler for /api/sysctl-dev-subtree-audit (R&D #73.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_sysctl_dev_subtree_audit_status(ctx: dict) -> Response:
    from ..modules import sysctl_dev_subtree_audit
    return 200, sysctl_dev_subtree_audit.status(ctx.get("config"))
