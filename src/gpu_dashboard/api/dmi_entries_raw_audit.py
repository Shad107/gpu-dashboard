"""HTTP handler for /api/dmi-entries-raw-audit (R&D #72.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_dmi_entries_raw_audit_status(ctx: dict) -> Response:
    from ..modules import dmi_entries_raw_audit
    return 200, dmi_entries_raw_audit.status(ctx.get("config"))
