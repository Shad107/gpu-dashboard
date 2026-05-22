"""HTTP handlers for HMAC bearer tokens + signed share-links + audit log.

Extracted from the legacy monolith in cycle 2 of the api/ split.
Covers R&D #9.3 (auth tokens + share-links) and R&D #9.6 (audit log).
"""
from __future__ import annotations

from typing import Optional, Tuple

Response = Tuple[int, dict]


# ─── R&D #9.3 — Auth tokens + share links ────────────────────────────────────


def handle_auth_tokens_list(ctx: dict) -> Response:
    """List tokens (secrets are NEVER returned)."""
    from ..modules import auth_tokens as _at
    tokens = _at.load_tokens()
    out = []
    for t in tokens:
        out.append({
            "id": t.get("id"),
            "name": t.get("name"),
            "scope": t.get("scope"),
            "created_ts": t.get("created_ts"),
            "expires_ts": t.get("expires_ts"),
        })
    return 200, {"ok": True, "tokens": out, "scopes_supported": ["read", "write", "admin"]}


def handle_auth_token_create(ctx: dict, payload: dict) -> Response:
    """Create a token. Returns the raw secret ONCE in the response."""
    from ..modules import auth_tokens as _at
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "payload must be a dict"}
    name = str(payload.get("name", "")).strip() or "unnamed"
    scope = str(payload.get("scope", "read"))
    ttl_raw = payload.get("ttl_s")
    try:
        ttl = int(ttl_raw) if ttl_raw is not None else None
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "ttl_s must be an integer"}
    try:
        rec, raw = _at.create_token(name=name, scope=scope, ttl_s=ttl)
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    return 200, {
        "ok": True,
        "id": rec["id"],
        "secret": raw,
        "scope": rec["scope"],
        "warning": "Save this secret now. It will not be retrievable later.",
    }


def handle_auth_token_delete(ctx: dict, token_id: str) -> Response:
    from ..modules import auth_tokens as _at
    if _at.delete_token(token_id):
        return 200, {"ok": True, "deleted": token_id}
    return 404, {"ok": False, "error": "token not found"}


def handle_auth_share_create(ctx: dict, payload: dict) -> Response:
    """Generate a stateless share-link. No DB row — entirely signed payload."""
    from ..modules import auth_tokens as _at
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "payload must be a dict"}
    scope = str(payload.get("scope", "read"))
    try:
        ttl = int(payload.get("ttl_s", 86400))
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "ttl_s must be an integer"}
    if ttl < 60 or ttl > 30 * 86400:
        return 400, {"ok": False, "error": "ttl_s must be in [60, 2592000]"}
    sub = str(payload.get("sub", "shared"))[:40]
    try:
        link = _at.make_share_link(scope=scope, ttl_s=ttl, sub=sub)
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    return 200, {"ok": True, "share_token": link, "scope": scope, "ttl_s": ttl, "sub": sub}


# ─── R&D #9.6 — Multi-user audit log ─────────────────────────────────────────


def handle_audit_log(ctx: dict, params: Optional[dict] = None) -> Response:
    """Read recent settings-mutation audit entries."""
    storage = ctx.get("storage")
    if not storage:
        return 503, {"ok": False, "error": "storage unavailable"}
    params = params or {}
    try:
        limit = max(1, min(500, int(params.get("limit", "100"))))
    except (ValueError, TypeError):
        limit = 100
    try:
        since_ts = int(params["since"]) if "since" in params else None
    except (ValueError, TypeError):
        since_ts = None
    rows = storage.get_audit_log(limit=limit, since_ts=since_ts)
    return 200, {"ok": True, "count": len(rows), "entries": rows}
