"""HTTP handler — R&D #102.1 Intel uncore freq auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_intel_uncore_freq_audit_status(
        ctx: dict) -> Response:
    from ..modules import intel_uncore_freq_audit
    return 200, intel_uncore_freq_audit.status(
        ctx.get("config"))
