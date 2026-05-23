"""HTTP handler for /api/kernel-build-config-audit (R&D #58.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_kernel_build_config_audit_status(ctx: dict) -> Response:
    from ..modules import kernel_build_config_audit
    return 200, kernel_build_config_audit.status(ctx.get("config"))
