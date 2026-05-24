"""HTTP handler — R&D #82.4 page_owner frag auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_page_owner_frag_audit_status(ctx: dict) -> Response:
    from ..modules import page_owner_frag_audit
    return 200, page_owner_frag_audit.status(ctx.get("config"))
