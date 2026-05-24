"""HTTP handler for /api/v4l2-media-audit (R&D #74.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_v4l2_media_audit_status(ctx: dict) -> Response:
    from ..modules import v4l2_media_audit
    return 200, v4l2_media_audit.status(ctx.get("config"))
