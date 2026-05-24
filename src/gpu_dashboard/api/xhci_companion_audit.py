"""HTTP handler — R&D #81.1 xHCI companion auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_xhci_companion_audit_status(ctx: dict) -> Response:
    from ..modules import xhci_companion_audit
    return 200, xhci_companion_audit.status(ctx.get("config"))
