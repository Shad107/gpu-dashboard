"""HTTP handler for /api/retired-pages (R&D #25.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_retired_pages_status(ctx: dict) -> Response:
    from ..modules import retired_pages
    return 200, retired_pages.status(ctx.get("config"))
