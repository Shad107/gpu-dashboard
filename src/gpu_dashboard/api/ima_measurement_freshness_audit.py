"""HTTP handler — R&D #104.4 IMA measurement freshness auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_ima_measurement_freshness_audit_status(
        ctx: dict) -> Response:
    from ..modules import ima_measurement_freshness_audit
    return 200, ima_measurement_freshness_audit.status(
        ctx.get("config"))
