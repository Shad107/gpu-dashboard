"""HTTP handler for /api/scsi-transport-audit (R&D #58.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_scsi_transport_audit_status(ctx: dict) -> Response:
    from ..modules import scsi_transport_audit
    return 200, scsi_transport_audit.status(ctx.get("config"))
