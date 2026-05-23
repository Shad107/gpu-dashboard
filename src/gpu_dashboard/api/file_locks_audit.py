"""HTTP handler for /api/file-locks-audit (R&D #42.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_file_locks_audit_status(ctx: dict) -> Response:
    from ..modules import file_locks_audit
    return 200, file_locks_audit.status(ctx.get("config"))
