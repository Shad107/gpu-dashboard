"""HTTP handler for /api/nvidia-rm-audit (R&D #47.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_nvidia_rm_audit_status(ctx: dict) -> Response:
    from ..modules import nvidia_rm_audit
    return 200, nvidia_rm_audit.status(ctx.get("config"))
