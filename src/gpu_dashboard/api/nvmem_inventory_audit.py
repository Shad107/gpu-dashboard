"""HTTP handler for /api/nvmem-inventory-audit (R&D #69.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_nvmem_inventory_audit_status(ctx: dict) -> Response:
    from ..modules import nvmem_inventory_audit
    return 200, nvmem_inventory_audit.status(ctx.get("config"))
