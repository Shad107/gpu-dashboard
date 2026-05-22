"""HTTP handler for /api/lm-studio/inventory (R&D #16.7)."""
from __future__ import annotations

from typing import Optional, Tuple


Response = Tuple[int, dict]


def handle_lm_studio_inventory(ctx: dict, params: Optional[dict] = None) -> Response:
    from ..modules import lm_studio
    return 200, lm_studio.status()
