"""HTTP handler — R&D #87.1 USB authorized_default auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_usb_authorized_default_audit_status(ctx: dict) -> Response:
    from ..modules import usb_authorized_default_audit
    return 200, usb_authorized_default_audit.status(ctx.get("config"))
