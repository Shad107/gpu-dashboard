"""HTTP handlers for /api/tdp-auto (R&D #17.3)."""
from __future__ import annotations

from typing import Optional, Tuple


Response = Tuple[int, dict]


def handle_tdp_auto_status(ctx: dict) -> Response:
    from ..modules import tdp_auto
    return 200, tdp_auto.status()


def handle_tdp_auto_save(ctx: dict, payload: dict) -> Response:
    from ..modules import tdp_auto
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "payload must be a dict"}
    tdp_auto.save_config(payload)
    return 200, {"ok": True}


def handle_tdp_auto_evaluate(ctx: dict, params: Optional[dict] = None) -> Response:
    from ..modules import tdp_auto
    sampler = ctx.get("sampler")
    samples = []
    if sampler:
        try:
            samples = sampler.snapshot()
        except Exception:
            pass
    params = params or {}
    dry_run = params.get("dry_run", "1") in ("1", "true", "True")
    return 200, tdp_auto.evaluate(samples, dry_run=dry_run)


def handle_tdp_auto_preview(ctx: dict, params: Optional[dict] = None) -> Response:
    from ..modules import tdp_auto
    sampler = ctx.get("sampler")
    samples = []
    if sampler:
        try:
            samples = sampler.snapshot()
        except Exception:
            pass
    params = params or {}
    try:
        window_s = max(60, min(86400, int(params.get("window_s", "3600"))))
    except (ValueError, TypeError):
        window_s = 3600
    return 200, tdp_auto.dry_run_preview(samples, window_s=window_s)
