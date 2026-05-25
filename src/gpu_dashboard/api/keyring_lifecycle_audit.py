"""HTTP handler — R&D #98.4 keyring lifecycle auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_keyring_lifecycle_audit_status(
        ctx: dict) -> Response:
    from ..modules import keyring_lifecycle_audit
    return 200, keyring_lifecycle_audit.status(
        ctx.get("config"))
