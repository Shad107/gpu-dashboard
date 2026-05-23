"""HTTP handler for /api/pcie-aer-trend (R&D #38.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_pcie_aer_trend_status(ctx: dict) -> Response:
    from ..modules import pcie_aer_trend
    return 200, pcie_aer_trend.status(ctx.get("config"))
