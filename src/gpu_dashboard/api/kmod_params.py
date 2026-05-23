"""HTTP handler for /api/kmod-params (R&D #29.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_kmod_params_status(ctx: dict) -> Response:
    from ..modules import kmod_params
    return 200, kmod_params.status(ctx.get("config"))
