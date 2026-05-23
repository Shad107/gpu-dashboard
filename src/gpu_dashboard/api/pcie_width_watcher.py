"""HTTP handler for /api/pcie-width-watcher (R&D #26.5)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_pcie_width_watcher_status(ctx: dict) -> Response:
    from ..modules import pcie_width_watcher
    return 200, pcie_width_watcher.status(ctx.get("config"))
