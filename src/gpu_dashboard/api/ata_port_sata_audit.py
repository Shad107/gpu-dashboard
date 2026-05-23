"""HTTP handler for /api/ata-port-sata-audit (R&D #71.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_ata_port_sata_audit_status(ctx: dict) -> Response:
    from ..modules import ata_port_sata_audit
    return 200, ata_port_sata_audit.status(ctx.get("config"))
