"""HTTP handler for /api/edac-ram-ecc (R&D #41.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_edac_ram_ecc_status(ctx: dict) -> Response:
    from ..modules import edac_ram_ecc
    return 200, edac_ram_ecc.status(ctx.get("config"))
