"""HTTP handler — R&D #98.3 FUSE connections auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_fuse_connections_audit_status(
        ctx: dict) -> Response:
    from ..modules import fuse_connections_audit
    return 200, fuse_connections_audit.status(
        ctx.get("config"))
