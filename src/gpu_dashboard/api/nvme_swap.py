"""HTTP handler for /api/nvme-swap (R&D #18.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_nvme_swap_status(ctx: dict) -> Response:
    from ..modules import nvme_swap
    return 200, nvme_swap.status(ctx.get("config"))
