"""HTTP handler for /api/persistence-mode (R&D #21.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_persistence_mode_status(ctx: dict) -> Response:
    from ..modules import persistence_mode
    return 200, persistence_mode.status(ctx.get("config"))
