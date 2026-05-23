"""HTTP handler for /api/nvme-iosched (R&D #30.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_nvme_iosched_status(ctx: dict) -> Response:
    from ..modules import nvme_iosched
    return 200, nvme_iosched.status(ctx.get("config"))
