"""HTTP handler — R&D #98.2 filesystem quota / projid auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_fs_quota_projid_audit_status(
        ctx: dict) -> Response:
    from ..modules import fs_quota_projid_audit
    return 200, fs_quota_projid_audit.status(
        ctx.get("config"))
