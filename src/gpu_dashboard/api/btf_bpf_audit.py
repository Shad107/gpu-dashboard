"""HTTP handler for /api/btf-bpf-audit (R&D #66.2)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_btf_bpf_audit_status(ctx: dict) -> Response:
    from ..modules import btf_bpf_audit
    return 200, btf_bpf_audit.status(ctx.get("config"))
