"""HTTP handler — R&D #88.1 userspace hardening sysctls auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_userspace_hardening_sysctls_audit_status(
        ctx: dict) -> Response:
    from ..modules import userspace_hardening_sysctls_audit
    return 200, userspace_hardening_sysctls_audit.status(
        ctx.get("config"))
