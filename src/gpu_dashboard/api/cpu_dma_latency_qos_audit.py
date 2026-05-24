"""HTTP handler — R&D #82.2 cpu_dma_latency QoS auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_cpu_dma_latency_qos_audit_status(ctx: dict) -> Response:
    from ..modules import cpu_dma_latency_qos_audit
    return 200, cpu_dma_latency_qos_audit.status(ctx.get("config"))
