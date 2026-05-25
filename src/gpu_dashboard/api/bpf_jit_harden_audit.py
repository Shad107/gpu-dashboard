"""HTTP handler — R&D #102.4 BPF JIT harden auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_bpf_jit_harden_audit_status(
        ctx: dict) -> Response:
    from ..modules import bpf_jit_harden_audit
    return 200, bpf_jit_harden_audit.status(
        ctx.get("config"))
