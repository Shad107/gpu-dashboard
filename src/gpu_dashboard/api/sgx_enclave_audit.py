"""HTTP handler for /api/sgx-enclave-audit (R&D #75.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_sgx_enclave_audit_status(ctx: dict) -> Response:
    from ..modules import sgx_enclave_audit
    return 200, sgx_enclave_audit.status(ctx.get("config"))
