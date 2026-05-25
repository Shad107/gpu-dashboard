"""HTTP handler — R&D #100.4 zswap deep pool auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_zswap_deep_pool_audit_status(
        ctx: dict) -> Response:
    from ..modules import zswap_deep_pool_audit
    return 200, zswap_deep_pool_audit.status(
        ctx.get("config"))
