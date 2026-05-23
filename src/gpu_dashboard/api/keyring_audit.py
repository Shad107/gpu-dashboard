"""HTTP handler for /api/keyring-audit (R&D #46.4)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_keyring_audit_status(ctx: dict) -> Response:
    from ..modules import keyring_audit
    return 200, keyring_audit.status(ctx.get("config"))
