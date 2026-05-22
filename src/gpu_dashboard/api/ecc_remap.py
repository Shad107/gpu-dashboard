"""HTTP handlers for /api/ecc-remap (R&D #17.1)."""
from __future__ import annotations

from typing import Optional, Tuple


Response = Tuple[int, dict]


def handle_ecc_remap_status(ctx: dict) -> Response:
    from ..modules import ecc_remap
    return 200, ecc_remap.status()


def handle_ecc_remap_record(ctx: dict, params: Optional[dict] = None) -> Response:
    """Trigger a fresh snapshot + persist."""
    from ..modules import ecc_remap
    return 200, ecc_remap.record_snapshot()


def handle_ecc_remap_rma_csv(ctx: dict) -> Tuple[int, str]:
    """RMA-friendly CSV summary."""
    from ..modules import ecc_remap
    return 200, ecc_remap.rma_report_csv()
