"""HTTP handler — R&D #108.1 nvidia_drm params auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_nvidia_drm_params_audit_status(
        ctx: dict) -> Response:
    from ..modules import nvidia_drm_params_audit
    return 200, nvidia_drm_params_audit.status(
        ctx.get("config"))
