"""HTTP handler for /api/carbon (R&D #13.4)."""
from __future__ import annotations

from typing import Optional, Tuple

from . import _core as _m


Response = Tuple[int, dict]


def _gpu_card_snapshot(*args, **kw):
    return _m._gpu_card_snapshot(*args, **kw)


def handle_carbon(ctx: dict, params: Optional[dict] = None) -> Response:
    """Return current carbon snapshot. Pulls kwh_today/month from the cost
    handler + live watts + tps from LLM perf handler when available."""
    from ..modules import carbon as _c

    snap = _gpu_card_snapshot(gpu_index=0)
    watts_now = None
    if snap and snap.get("alive"):
        p = snap.get("power")
        if p is not None:
            watts_now = float(p)

    # Pull kwh_today + kwh_month via cost.handle_electricity
    kwh_today = kwh_month = None
    try:
        from . import cost as _cost
        _, e = _cost.handle_electricity(ctx, {"since": "3600"})
        if isinstance(e, dict) and e.get("ok"):
            kwh_today = e.get("daily_kwh")
            kwh_month = e.get("kwh_month") or e.get("monthly_kwh")
    except Exception:
        pass

    # Pull tps_now via llm.handle_llm_perf
    tps_now = None
    try:
        from . import llm as _llm
        _, perf = _llm.handle_llm_perf(ctx, {})
        if isinstance(perf, dict) and perf.get("available"):
            tps_now = perf.get("avg_tps_1m")
    except Exception:
        tps_now = None

    return 200, _c.status(
        cfg=ctx.get("config"),
        kwh_today=kwh_today,
        kwh_month=kwh_month,
        watts_now=watts_now,
        tps_now=tps_now,
    )
