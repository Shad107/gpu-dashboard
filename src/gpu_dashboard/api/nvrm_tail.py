"""HTTP handler for /api/nvrm-tail (R&D #28.7)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_nvrm_tail_status(ctx: dict) -> Response:
    from ..modules import nvrm_tail
    return 200, nvrm_tail.status(ctx.get("config"))
