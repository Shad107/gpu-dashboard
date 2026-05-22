"""HTTP handler for /api/wall-meter (R&D #12.1).

Returns the configured smart-plug reading + computed PSU efficiency.
"""
from __future__ import annotations

from typing import Optional, Tuple

from . import _core as _m


Response = Tuple[int, dict]


def _gpu_card_snapshot(*args, **kw):
    return _m._gpu_card_snapshot(*args, **kw)


def handle_wall_meter(ctx: dict, params: Optional[dict] = None) -> Response:
    """Read the configured wall-meter (Shelly/Tasmota) + return PSU efficiency
    relative to GPU power draw."""
    from ..modules import wall_meter as wm
    cfg = ctx.get("config")
    if cfg is None:
        return 503, {"ok": False, "error": "no config"}
    snap = _gpu_card_snapshot(gpu_index=0)
    gpu_w = float(snap.get("power")) if snap and snap.get("alive") and snap.get("power") is not None else None
    return 200, wm.status(cfg, gpu_w=gpu_w)
