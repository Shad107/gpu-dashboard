"""HTTP handler for /api/acpi-audit (R&D #47.2)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_acpi_audit_status(ctx: dict) -> Response:
    from ..modules import acpi_audit
    return 200, acpi_audit.status(ctx.get("config"))
