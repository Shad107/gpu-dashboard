"""HTTP handler for /api/throttle-bits (R&D #25.5)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_throttle_bits_status(ctx: dict) -> Response:
    from ..modules import throttle_bits
    return 200, throttle_bits.status(ctx.get("config"))
