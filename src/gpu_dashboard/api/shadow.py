"""F3 — Shadow telemetry HTTP handler."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_shadow_telemetry(ctx: dict) -> Response:
    from ..modules import shadow_telemetry
    return 200, shadow_telemetry.sample(ctx["config"])
