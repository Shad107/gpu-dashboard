"""HTTP handler for /api/io-uring-runtime-audit (R&D #54.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_io_uring_runtime_audit_status(ctx: dict) -> Response:
    from ..modules import io_uring_runtime_audit
    return 200, io_uring_runtime_audit.status(ctx.get("config"))
