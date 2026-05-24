"""HTTP handler — R&D #80.3 btrfs allocator auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_btrfs_allocator_audit_status(ctx: dict) -> Response:
    from ..modules import btrfs_allocator_audit
    return 200, btrfs_allocator_audit.status(ctx.get("config"))
