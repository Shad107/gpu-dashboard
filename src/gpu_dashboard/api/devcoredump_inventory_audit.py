"""HTTP handler for /api/devcoredump-inventory-audit (R&D #70.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_devcoredump_inventory_audit_status(ctx: dict) -> Response:
    from ..modules import devcoredump_inventory_audit
    return 200, devcoredump_inventory_audit.status(ctx.get("config"))
