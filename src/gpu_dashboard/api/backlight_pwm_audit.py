"""HTTP handler for /api/backlight-pwm-audit (R&D #57.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_backlight_pwm_audit_status(ctx: dict) -> Response:
    from ..modules import backlight_pwm_audit
    return 200, backlight_pwm_audit.status(ctx.get("config"))
