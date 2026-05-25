"""HTTP handler — R&D #101.3 fscache + cachefiles auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_fscache_cachefiles_audit_status(
        ctx: dict) -> Response:
    from ..modules import fscache_cachefiles_audit
    return 200, fscache_cachefiles_audit.status(
        ctx.get("config"))
