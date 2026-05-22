"""HTTP handlers for /api/tariff (R&D #15.2)."""
from __future__ import annotations

from typing import Optional, Tuple


Response = Tuple[int, dict]


def handle_tariff_status(ctx: dict) -> Response:
    from ..modules import tariff
    return 200, tariff.status()


def handle_tariff_estimate(ctx: dict, params: Optional[dict] = None) -> Response:
    """Estimate the cost of a job.

    Query params :
      watts = average watts (required)
      duration_s = duration in seconds (required)
      start_hour = optional hour 0..23 (default = now)
    """
    from ..modules import tariff
    params = params or {}
    try:
        watts = float(params.get("watts", "0"))
        duration_s = float(params.get("duration_s", "0"))
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "watts and duration_s required, numeric"}
    if watts <= 0 or duration_s <= 0:
        return 400, {"ok": False, "error": "watts and duration_s must be > 0"}
    start_hour = None
    if "start_hour" in params:
        try:
            start_hour = int(params["start_hour"]) % 24
        except (ValueError, TypeError):
            start_hour = None
    est = tariff.estimate_job_cost(watts, duration_s, start_hour=start_hour)
    if est is None:
        return 200, {"ok": True, "available": False,
                     "reason": "no tariffs CSV configured"}
    return 200, {"ok": True, "available": True, **est}


def handle_tariff_cheapest(ctx: dict, params: Optional[dict] = None) -> Response:
    """Find the cheapest start time for a job over the next 24h.

    Query params :
      watts, duration_s, within_h (default 24)
    """
    from ..modules import tariff
    params = params or {}
    try:
        watts = float(params.get("watts", "0"))
        duration_s = float(params.get("duration_s", "0"))
        within_h = max(1, min(48, int(params.get("within_h", "24"))))
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "watts, duration_s, within_h required, numeric"}
    if watts <= 0 or duration_s <= 0:
        return 400, {"ok": False, "error": "watts and duration_s must be > 0"}
    result = tariff.find_cheapest_start(watts, duration_s, within_h=within_h)
    if result is None:
        return 200, {"ok": True, "available": False,
                     "reason": "no tariffs CSV configured"}
    return 200, {"ok": True, "available": True, **result}
