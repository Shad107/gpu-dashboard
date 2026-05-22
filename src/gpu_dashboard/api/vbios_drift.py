"""HTTP handlers for /api/vbios-drift (R&D #20.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_vbios_drift_status(ctx: dict) -> Response:
    from ..modules import vbios_drift
    return 200, vbios_drift.status(ctx.get("config"))


def handle_vbios_drift_rebaseline(ctx: dict, payload: dict) -> Response:
    """POST /api/vbios-drift/rebaseline → snapshot current state as
    the new baseline. Intended for use right after a known-good flash."""
    from ..modules import vbios_drift
    new_baseline = vbios_drift.rebaseline()
    return 200, {"ok": True, "baseline_size": len(new_baseline)}
