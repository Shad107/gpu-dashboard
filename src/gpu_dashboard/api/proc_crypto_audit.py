"""HTTP handler for /api/proc-crypto-audit (R&D #56.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_proc_crypto_audit_status(ctx: dict) -> Response:
    from ..modules import proc_crypto_audit
    return 200, proc_crypto_audit.status(ctx.get("config"))
