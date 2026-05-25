"""HTTP handler — R&D #105.1 IMA digest_lists auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_ima_digest_lists_audit_status(
        ctx: dict) -> Response:
    from ..modules import ima_digest_lists_audit
    return 200, ima_digest_lists_audit.status(
        ctx.get("config"))
