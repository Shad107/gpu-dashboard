"""HTTP handler — R&D #105.2 DRM GT load status auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_drm_gt_load_status_audit_status(
        ctx: dict) -> Response:
    from ..modules import drm_gt_load_status_audit
    return 200, drm_gt_load_status_audit.status(
        ctx.get("config"))
