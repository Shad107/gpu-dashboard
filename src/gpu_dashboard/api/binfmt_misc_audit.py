"""HTTP handler for /api/binfmt-misc-audit (R&D #63.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_binfmt_misc_audit_status(ctx: dict) -> Response:
    from ..modules import binfmt_misc_audit
    return 200, binfmt_misc_audit.status(ctx.get("config"))
