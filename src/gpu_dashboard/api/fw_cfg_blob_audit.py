"""HTTP handler for /api/fw-cfg-blob-audit (R&D #72.1)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_fw_cfg_blob_audit_status(ctx: dict) -> Response:
    from ..modules import fw_cfg_blob_audit
    return 200, fw_cfg_blob_audit.status(ctx.get("config"))
