"""HTTP handler for /api/leds-class-audit (R&D #63.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_leds_class_audit_status(ctx: dict) -> Response:
    from ..modules import leds_class_audit
    return 200, leds_class_audit.status(ctx.get("config"))
