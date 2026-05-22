"""HTTP handler for /api/disk-health (R&D #12.2)."""
from __future__ import annotations

from typing import Optional, Tuple


Response = Tuple[int, dict]


def handle_disk_health(ctx: dict) -> Response:
    """Return SMART health snapshot for detected disks."""
    from ..modules import disk_health
    return 200, disk_health.status()
