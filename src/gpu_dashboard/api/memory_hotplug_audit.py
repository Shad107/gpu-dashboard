"""HTTP handler for /api/memory-hotplug-audit (R&D #62.3)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_memory_hotplug_audit_status(ctx: dict) -> Response:
    from ..modules import memory_hotplug_audit
    return 200, memory_hotplug_audit.status(ctx.get("config"))
