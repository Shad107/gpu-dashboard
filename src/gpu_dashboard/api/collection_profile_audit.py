"""HTTP handler — Hardening #2 collection profile."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_collection_profile_audit_status(
        ctx: dict) -> Response:
    from ..modules import collection_profile_audit
    return 200, collection_profile_audit.status(
        ctx.get("config"))
