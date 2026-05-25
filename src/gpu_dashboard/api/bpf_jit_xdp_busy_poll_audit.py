"""HTTP handler — R&D #93.4 BPF JIT + XDP + busy poll auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_bpf_jit_xdp_busy_poll_audit_status(
        ctx: dict) -> Response:
    from ..modules import bpf_jit_xdp_busy_poll_audit
    return 200, bpf_jit_xdp_busy_poll_audit.status(
        ctx.get("config"))
