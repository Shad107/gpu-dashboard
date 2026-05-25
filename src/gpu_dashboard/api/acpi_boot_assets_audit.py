"""HTTP handler — R&D #109.2 ACPI boot assets auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_acpi_boot_assets_audit_status(
        ctx: dict) -> Response:
    from ..modules import acpi_boot_assets_audit
    return 200, acpi_boot_assets_audit.status(
        ctx.get("config"))
