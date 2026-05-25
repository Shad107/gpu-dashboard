"""HTTP handler — R&D #108.3 dm_mod params auditor."""
from __future__ import annotations

from typing import Any, Tuple

Response = Tuple[int, Any]


def handle_dm_mod_params_audit_status(
        ctx: dict) -> Response:
    from ..modules import dm_mod_params_audit
    return 200, dm_mod_params_audit.status(
        ctx.get("config"))
