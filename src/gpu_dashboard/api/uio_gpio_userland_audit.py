"""HTTP handler for /api/uio-gpio-userland-audit (R&D #70.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_uio_gpio_userland_audit_status(ctx: dict) -> Response:
    from ..modules import uio_gpio_userland_audit
    return 200, uio_gpio_userland_audit.status(ctx.get("config"))
