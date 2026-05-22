"""HTTP handler for /api/pcie-histogram (R&D #18.6)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_pcie_histogram_status(ctx: dict) -> Response:
    from ..modules import pcie_histogram
    return 200, pcie_histogram.status(ctx.get("config"))
