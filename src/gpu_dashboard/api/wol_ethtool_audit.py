"""HTTP handler — R&D #86.1 WoL ethtool auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_wol_ethtool_audit_status(ctx: dict) -> Response:
    from ..modules import wol_ethtool_audit
    return 200, wol_ethtool_audit.status(ctx.get("config"))
