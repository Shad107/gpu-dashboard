"""HTTP handler for /api/sd-cache-janitor (R&D #21.5)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_sd_cache_janitor_status(ctx: dict) -> Response:
    from ..modules import sd_cache_janitor
    return 200, sd_cache_janitor.status(ctx.get("config"))
