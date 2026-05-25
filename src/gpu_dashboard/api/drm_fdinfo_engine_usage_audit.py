"""HTTP handler — R&D #92.4 DRM fdinfo per-client VRAM auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_drm_fdinfo_engine_usage_audit_status(
        ctx: dict) -> Response:
    from ..modules import drm_fdinfo_engine_usage_audit
    return 200, drm_fdinfo_engine_usage_audit.status(
        ctx.get("config"))
