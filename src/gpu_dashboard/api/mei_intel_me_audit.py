"""HTTP handler for /api/mei-intel-me-audit (R&D #62.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_mei_intel_me_audit_status(ctx: dict) -> Response:
    from ..modules import mei_intel_me_audit
    return 200, mei_intel_me_audit.status(ctx.get("config"))
