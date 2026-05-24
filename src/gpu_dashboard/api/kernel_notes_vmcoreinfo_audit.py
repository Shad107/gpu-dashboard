"""HTTP handler for /api/kernel-notes-vmcoreinfo-audit (R&D #73.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_kernel_notes_vmcoreinfo_audit_status(ctx: dict) -> Response:
    from ..modules import kernel_notes_vmcoreinfo_audit
    return 200, kernel_notes_vmcoreinfo_audit.status(ctx.get("config"))
