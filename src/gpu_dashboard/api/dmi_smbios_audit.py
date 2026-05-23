"""HTTP handler for /api/dmi-smbios-audit (R&D #59.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_dmi_smbios_audit_status(ctx: dict) -> Response:
    from ..modules import dmi_smbios_audit
    return 200, dmi_smbios_audit.status(ctx.get("config"))
