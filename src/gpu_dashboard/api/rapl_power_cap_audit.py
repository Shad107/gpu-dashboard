"""HTTP handler for /api/rapl-power-cap-audit (R&D #53.4)."""
from __future__ import annotations

from typing import Tuple

Response = Tuple[int, dict]


def handle_rapl_power_cap_audit_status(ctx: dict) -> Response:
    from ..modules import rapl_power_cap_audit
    return 200, rapl_power_cap_audit.status(ctx.get("config"))
