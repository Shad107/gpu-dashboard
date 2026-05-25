"""HTTP handler — R&D #106.1 io_delay_type auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_io_delay_type_audit_status(
        ctx: dict) -> Response:
    from ..modules import io_delay_type_audit
    return 200, io_delay_type_audit.status(
        ctx.get("config"))
