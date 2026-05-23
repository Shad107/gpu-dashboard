"""HTTP handler for /api/dkms-status (R&D #24.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_dkms_status(ctx: dict) -> Response:
    from ..modules import dkms_status
    return 200, dkms_status.status(ctx.get("config"))
