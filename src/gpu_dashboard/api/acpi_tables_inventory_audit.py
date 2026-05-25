"""HTTP handler — R&D #109.3 ACPI tables inventory auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_acpi_tables_inventory_audit_status(
        ctx: dict) -> Response:
    from ..modules import acpi_tables_inventory_audit
    return 200, acpi_tables_inventory_audit.status(
        ctx.get("config"))
