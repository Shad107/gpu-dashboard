"""HTTP handler for /api/vm-tuning-deep (R&D #40.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_vm_tuning_deep_status(ctx: dict) -> Response:
    from ..modules import vm_tuning_deep
    return 200, vm_tuning_deep.status(ctx.get("config"))
