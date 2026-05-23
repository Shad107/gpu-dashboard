"""HTTP handler for /api/hugepages-audit (R&D #54.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_hugepages_audit_status(ctx: dict) -> Response:
    from ..modules import hugepages_audit
    return 200, hugepages_audit.status(ctx.get("config"))
