"""F5.1 — Health Strip HTTP handler."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_health_strip(ctx: dict) -> Response:
    from ..modules import health_strip
    return 200, health_strip.aggregate(ctx["config"])
