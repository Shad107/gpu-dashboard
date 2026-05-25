"""HTTP handler — R&D #106.3 L2 cache imbalance auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_cache_l2_imbalance_audit_status(
        ctx: dict) -> Response:
    from ..modules import cache_l2_imbalance_audit
    return 200, cache_l2_imbalance_audit.status(
        ctx.get("config"))
