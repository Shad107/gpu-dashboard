"""HTTP handler — R&D #97.1 KVM MMU auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_kvm_mmu_audit_status(ctx: dict) -> Response:
    from ..modules import kvm_mmu_audit
    return 200, kvm_mmu_audit.status(ctx.get("config"))
