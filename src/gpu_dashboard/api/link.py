"""F7 — Link Stable Mode HTTP handlers."""
from __future__ import annotations

from typing import Any, Optional, Tuple

Response = Tuple[int, Any]


def handle_link_stable_status(ctx: dict) -> Response:
    from ..modules import link_stable
    return 200, link_stable.status(ctx["config"])


def handle_link_stable_enable(ctx: dict,
                                body: Optional[dict] = None) -> Response:
    from ..modules import link_stable
    body = body or {}
    min_mhz = body.get("min_mhz", link_stable.DEFAULT_MIN_MHZ)
    max_mhz = body.get("max_mhz", link_stable.DEFAULT_MAX_MHZ)
    return 200, link_stable.enable(min_mhz=min_mhz, max_mhz=max_mhz)


def handle_link_stable_disable(ctx: dict,
                                 body: Optional[dict] = None) -> Response:
    from ..modules import link_stable
    return 200, link_stable.disable()
