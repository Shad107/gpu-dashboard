"""HTTP handler for /api/inference-cost (R&D #14.4)."""
from __future__ import annotations

from typing import Optional, Tuple


Response = Tuple[int, dict]


def handle_inference_cost(ctx: dict, params: Optional[dict] = None) -> Response:
    """Compute marginal inference cost over rolling windows.

    Query params :
      windows = comma-separated seconds (default '60,600,3600,86400')
    """
    from ..modules import inference_cost as ic
    storage = ctx.get("storage")
    cfg = ctx.get("config")
    params = params or {}
    windows = None
    if "windows" in params:
        try:
            windows = [int(s) for s in str(params["windows"]).split(",")
                       if s.strip().isdigit()]
        except (ValueError, TypeError):
            windows = None
    return 200, ic.status(storage, cfg, windows=windows)
