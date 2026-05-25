"""HTTP handler — R&D #93.1 pipe/mqueue/epoll limits auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_pipe_mqueue_limits_audit_status(
        ctx: dict) -> Response:
    from ..modules import pipe_mqueue_limits_audit
    return 200, pipe_mqueue_limits_audit.status(
        ctx.get("config"))
