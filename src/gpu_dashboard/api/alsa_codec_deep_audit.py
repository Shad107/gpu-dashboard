"""HTTP handler for /api/alsa-codec-deep-audit (R&D #61.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_alsa_codec_deep_audit_status(ctx: dict) -> Response:
    from ..modules import alsa_codec_deep_audit
    return 200, alsa_codec_deep_audit.status(ctx.get("config"))
