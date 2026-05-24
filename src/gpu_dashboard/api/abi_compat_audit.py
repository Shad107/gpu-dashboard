"""HTTP handler for /api/abi-compat-audit (R&D #74.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_abi_compat_audit_status(ctx: dict) -> Response:
    from ..modules import abi_compat_audit
    return 200, abi_compat_audit.status(ctx.get("config"))
