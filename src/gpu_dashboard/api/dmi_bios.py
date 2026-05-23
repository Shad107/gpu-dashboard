"""HTTP handler for /api/dmi-bios (R&D #30.5)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_dmi_bios_status(ctx: dict) -> Response:
    from ..modules import dmi_bios
    return 200, dmi_bios.status(ctx.get("config"))
