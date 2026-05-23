"""HTTP handler for /api/vram-leak (R&D #22.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_vram_leak_status(ctx: dict) -> Response:
    from ..modules import vram_leak
    return 200, vram_leak.status(ctx.get("config"))
