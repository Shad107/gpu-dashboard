"""HTTP handler for /api/pcie-aer (R&D #24.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_pcie_aer_status(ctx: dict) -> Response:
    from ..modules import pcie_aer
    return 200, pcie_aer.status(ctx.get("config"))
