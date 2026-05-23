"""HTTP handler for /api/bug-report-prep (R&D #25.3)."""
from __future__ import annotations

from typing import Tuple


Response = Tuple[int, dict]


def handle_bug_report_prep_status(ctx: dict) -> Response:
    from ..modules import bug_report_prep
    return 200, bug_report_prep.status(ctx.get("config"))
