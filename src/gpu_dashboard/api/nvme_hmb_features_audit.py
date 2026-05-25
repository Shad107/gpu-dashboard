"""HTTP handler — R&D #91.3 NVMe HMB features auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_nvme_hmb_features_audit_status(
        ctx: dict) -> Response:
    from ..modules import nvme_hmb_features_audit
    return 200, nvme_hmb_features_audit.status(
        ctx.get("config"))
