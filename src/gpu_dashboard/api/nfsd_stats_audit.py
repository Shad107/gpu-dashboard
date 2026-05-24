"""HTTP handler — R&D #83.3 nfsd stats auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_nfsd_stats_audit_status(ctx: dict) -> Response:
    from ..modules import nfsd_stats_audit
    return 200, nfsd_stats_audit.status(ctx.get("config"))
