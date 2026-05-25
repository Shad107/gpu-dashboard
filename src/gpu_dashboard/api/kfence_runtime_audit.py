"""HTTP handler — R&D #101.1 KFENCE runtime auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_kfence_runtime_audit_status(
        ctx: dict) -> Response:
    from ..modules import kfence_runtime_audit
    return 200, kfence_runtime_audit.status(
        ctx.get("config"))
