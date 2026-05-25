"""HTTP handler — R&D #102.3 module sig_enforce auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_module_sig_enforce_audit_status(
        ctx: dict) -> Response:
    from ..modules import module_sig_enforce_audit
    return 200, module_sig_enforce_audit.status(
        ctx.get("config"))
