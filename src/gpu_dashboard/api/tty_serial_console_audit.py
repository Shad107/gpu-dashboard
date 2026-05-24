"""HTTP handler — R&D #84.4 TTY/serial console auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_tty_serial_console_audit_status(ctx: dict) -> Response:
    from ..modules import tty_serial_console_audit
    return 200, tty_serial_console_audit.status(ctx.get("config"))
