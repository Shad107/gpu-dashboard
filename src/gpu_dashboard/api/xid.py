"""HTTP handler for /api/xid (R&D #14.1)."""
from __future__ import annotations

from typing import Optional, Tuple


Response = Tuple[int, dict]


def handle_xid(ctx: dict, params: Optional[dict] = None) -> Response:
    """Return recent Xid events + per-severity counts.

    Query params :
      since = '24h' (default) | '1h' | '7d' etc.
      limit = max kernel-log lines to scan (default 200)
    """
    from ..modules import xid_decoder
    params = params or {}
    since = params.get("since", "24h")
    try:
        limit = max(50, min(2000, int(params.get("limit", "200"))))
    except (ValueError, TypeError):
        limit = 200
    events = xid_decoder.decode_recent_journal(since=since, limit=limit)
    return 200, xid_decoder.stats(events)


def handle_xid_decode(ctx: dict, params: Optional[dict] = None) -> Response:
    """Decode a SINGLE Xid code passed as ?code=N. Useful for tooltips."""
    from ..modules import xid_decoder
    params = params or {}
    try:
        code = int(params.get("code", "-1"))
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "code must be an integer"}
    if code < 0:
        return 400, {"ok": False, "error": "code required"}
    return 200, {"ok": True, **xid_decoder.decode(code)}
