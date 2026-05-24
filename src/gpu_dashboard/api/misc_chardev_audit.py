"""HTTP handler for /api/misc-chardev-audit (R&D #75.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_misc_chardev_audit_status(ctx: dict) -> Response:
    from ..modules import misc_chardev_audit
    return 200, misc_chardev_audit.status(ctx.get("config"))
