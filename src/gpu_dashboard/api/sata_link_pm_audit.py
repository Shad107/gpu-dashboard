"""HTTP handler for /api/sata-link-pm-audit (R&D #56.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_sata_link_pm_audit_status(ctx: dict) -> Response:
    from ..modules import sata_link_pm_audit
    return 200, sata_link_pm_audit.status(ctx.get("config"))
