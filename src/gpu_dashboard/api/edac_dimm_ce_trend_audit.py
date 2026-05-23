"""HTTP handler for /api/edac-dimm-ce-trend-audit (R&D #71.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_edac_dimm_ce_trend_audit_status(ctx: dict) -> Response:
    from ..modules import edac_dimm_ce_trend_audit
    return 200, edac_dimm_ce_trend_audit.status(ctx.get("config"))
