"""HTTP handler for /api/usage/users (R&D #14.2)."""
from __future__ import annotations

from typing import Optional, Tuple

from . import _core as _m


Response = Tuple[int, dict]


def _gpu_card_snapshot(*args, **kw):
    return _m._gpu_card_snapshot(*args, **kw)


def handle_lab_usage_live(ctx: dict, params: Optional[dict] = None) -> Response:
    """Return a SINGLE current sample of per-user usage.

    Query params :
      none yet — future : ?since=, ?until=, ?csv=1
    """
    from ..modules import lab_accounting as la
    snap = _gpu_card_snapshot(gpu_index=0)
    watts = None
    if snap and snap.get("alive"):
        p = snap.get("power")
        if p is not None:
            watts = float(p)
    return 200, la.evaluate(watts_total=watts)
