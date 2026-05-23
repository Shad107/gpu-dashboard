"""HTTP handler for /api/gpu-pci-bind (R&D #40.1)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_gpu_pci_bind_status(ctx: dict) -> Response:
    from ..modules import gpu_pci_bind
    return 200, gpu_pci_bind.status(ctx.get("config"))
