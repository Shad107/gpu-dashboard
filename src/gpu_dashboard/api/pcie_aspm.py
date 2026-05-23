"""HTTP handler for /api/pcie-aspm (R&D #23.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_pcie_aspm_status(ctx: dict) -> Response:
    from ..modules import pcie_aspm
    return 200, pcie_aspm.status(ctx.get("config"))
