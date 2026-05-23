"""HTTP handler for /api/alsa-cards-audit (R&D #58.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_alsa_cards_audit_status(ctx: dict) -> Response:
    from ..modules import alsa_cards_audit
    return 200, alsa_cards_audit.status(ctx.get("config"))
