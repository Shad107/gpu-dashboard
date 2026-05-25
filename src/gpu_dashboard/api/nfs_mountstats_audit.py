"""HTTP handler — R&D #93.3 NFS mountstats auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_nfs_mountstats_audit_status(
        ctx: dict) -> Response:
    from ..modules import nfs_mountstats_audit
    return 200, nfs_mountstats_audit.status(
        ctx.get("config"))
