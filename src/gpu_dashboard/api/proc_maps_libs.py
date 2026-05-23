"""HTTP handler for /api/proc-maps-libs (R&D #38.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_proc_maps_libs_status(ctx: dict) -> Response:
    from ..modules import proc_maps_libs
    return 200, proc_maps_libs.status(ctx.get("config"))
