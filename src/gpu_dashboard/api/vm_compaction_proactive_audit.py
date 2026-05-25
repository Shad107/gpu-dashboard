"""HTTP handler — R&D #105.4 vm compaction proactive auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_vm_compaction_proactive_audit_status(
        ctx: dict) -> Response:
    from ..modules import vm_compaction_proactive_audit
    return 200, vm_compaction_proactive_audit.status(
        ctx.get("config"))
