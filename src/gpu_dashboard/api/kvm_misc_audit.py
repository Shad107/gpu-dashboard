"""HTTP handler for /api/kvm-misc-audit (R&D #54.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_kvm_misc_audit_status(ctx: dict) -> Response:
    from ..modules import kvm_misc_audit
    return 200, kvm_misc_audit.status(ctx.get("config"))
