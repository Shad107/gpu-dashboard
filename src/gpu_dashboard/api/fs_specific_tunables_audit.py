"""HTTP handler for /api/fs-specific-tunables-audit (R&D #68.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_fs_specific_tunables_audit_status(ctx: dict) -> Response:
    from ..modules import fs_specific_tunables_audit
    return 200, fs_specific_tunables_audit.status(ctx.get("config"))
