"""HTTP handler — R&D #81.2 BPF program inventory auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_bpf_program_inventory_audit_status(ctx: dict) -> Response:
    from ..modules import bpf_program_inventory_audit
    return 200, bpf_program_inventory_audit.status(ctx.get("config"))
