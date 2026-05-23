"""HTTP handler for /api/security-posture (R&D #46.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_security_posture_status(ctx: dict) -> Response:
    from ..modules import security_posture
    return 200, security_posture.status(ctx.get("config"))
