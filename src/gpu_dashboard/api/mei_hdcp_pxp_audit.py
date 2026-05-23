"""HTTP handler for /api/mei-hdcp-pxp-audit (R&D #64.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_mei_hdcp_pxp_audit_status(ctx: dict) -> Response:
    from ..modules import mei_hdcp_pxp_audit
    return 200, mei_hdcp_pxp_audit.status(ctx.get("config"))
