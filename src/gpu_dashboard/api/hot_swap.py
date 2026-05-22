"""HTTP handlers for /api/hot-swap (R&D #14.5)."""
from __future__ import annotations

from typing import Optional, Tuple


Response = Tuple[int, dict]


def handle_hot_swap_status(ctx: dict) -> Response:
    """Return current PCIe + DRM state + recent events."""
    from ..modules import hot_swap
    return 200, hot_swap.status()


def handle_hot_swap_evaluate(ctx: dict, params: Optional[dict] = None) -> Response:
    """Trigger a fresh snapshot + diff against the persisted previous one.
    Caller can use this to manually refresh the event buffer."""
    from ..modules import hot_swap
    return 200, hot_swap.evaluate()
