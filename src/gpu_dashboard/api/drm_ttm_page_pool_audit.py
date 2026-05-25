"""HTTP handler — R&D #94.3 DRM TTM page pool auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_drm_ttm_page_pool_audit_status(
        ctx: dict) -> Response:
    from ..modules import drm_ttm_page_pool_audit
    return 200, drm_ttm_page_pool_audit.status(
        ctx.get("config"))
