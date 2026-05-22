"""HTTP handler for the /api/peers fleet endpoint (R&D #12.3)."""
from __future__ import annotations

from typing import Optional, Tuple


Response = Tuple[int, dict]


def handle_peers(ctx: dict, params: Optional[dict] = None) -> Response:
    """Return the LAN-discovered peers (other gpu-dashboard instances on
    the same broadcast domain).

    Query params :
      ttl_s = max age in seconds for a peer to still be 'alive' (default 30)
    """
    from ..modules import peer_beacon
    params = params or {}
    try:
        ttl_s = max(5, min(600, int(params.get("ttl_s", "30"))))
    except (ValueError, TypeError):
        ttl_s = 30
    peers = peer_beacon.registry().list(ttl_s=ttl_s)
    return 200, {"ok": True, "count": len(peers), "ttl_s": ttl_s, "peers": peers}
