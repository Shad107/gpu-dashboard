"""HTTP handler for /api/remoteproc-coprocessor-audit (R&D #70.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_remoteproc_coprocessor_audit_status(ctx: dict) -> Response:
    from ..modules import remoteproc_coprocessor_audit
    return 200, remoteproc_coprocessor_audit.status(ctx.get("config"))
