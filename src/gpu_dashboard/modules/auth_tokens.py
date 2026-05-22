"""Module auth_tokens — HMAC-based bearer tokens + share-links (R&D #9.3).

Two token systems live here :

1. **Bearer tokens** stored in ~/.config/gpu-dashboard/tokens.json :
   {tokens: [{id, name, secret_hash, scope, created_ts, expires_ts}, ...]}
   The secret itself is shown ONCE at creation time, then only its
   SHA-256 hash is persisted. Client sends 'Authorization: Bearer <secret>'.

2. **Share-links** : self-signed time-limited URLs. No DB row. Format :
   ?share=<base64-of-payload>.<hmac>
   Payload : {scope, exp, sub} JSON, signed with server's per-install secret.
   Useful for posting a read-only dashboard URL in chat without creating
   a stored token.

Scopes (hierarchical) :
  - "read"  : GET-only endpoints
  - "write" : read + POST/PUT mutations (fan curve, electricity config…)
  - "admin" : write + create/delete tokens, restart service

stdlib only : hashlib, hmac, secrets, base64, json, time.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional, Tuple


NAME = "auth_tokens"

_SCOPE_ORDER = {"read": 0, "write": 1, "admin": 2}
_DEFAULT_TTL_S = 30 * 86400  # 30 days


def tokens_path() -> str:
    return os.path.expanduser("~/.config/gpu-dashboard/tokens.json")


def server_secret_path() -> str:
    return os.path.expanduser("~/.config/gpu-dashboard/.server_secret")


def get_or_create_server_secret() -> bytes:
    """Read the per-install secret used to sign share-links. Created on
    first call. Stored as raw bytes (32) base64-encoded."""
    path = server_secret_path()
    if os.path.exists(path):
        try:
            with open(path) as f:
                return base64.b64decode(f.read().strip())
        except (OSError, ValueError):
            pass
    secret = secrets.token_bytes(32)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Restrictive perms — only owner reads
    with open(path, "w") as f:
        f.write(base64.b64encode(secret).decode("ascii"))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return secret


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def load_tokens() -> list:
    path = tokens_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("tokens"), list):
            return data["tokens"]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def save_tokens(tokens: list) -> None:
    path = tokens_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"tokens": tokens}, f, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def create_token(name: str, scope: str = "read",
                 ttl_s: Optional[int] = None) -> Tuple[dict, str]:
    """Create a new bearer token. Returns (record, raw_secret).
    Raw secret is shown ONCE — never recoverable from the JSON."""
    if scope not in _SCOPE_ORDER:
        raise ValueError(f"unknown scope {scope!r}, expected one of {list(_SCOPE_ORDER)}")
    raw = secrets.token_urlsafe(24)
    rec = {
        "id": secrets.token_hex(8),
        "name": name[:80],
        "secret_hash": _hash_secret(raw),
        "scope": scope,
        "created_ts": int(time.time()),
        "expires_ts": int(time.time() + ttl_s) if ttl_s else None,
    }
    tokens = load_tokens()
    tokens.append(rec)
    save_tokens(tokens)
    return rec, raw


def delete_token(token_id: str) -> bool:
    tokens = load_tokens()
    before = len(tokens)
    tokens = [t for t in tokens if t.get("id") != token_id]
    save_tokens(tokens)
    return len(tokens) < before


def verify_token(raw_secret: str) -> Optional[dict]:
    """Look up a raw bearer secret. Returns the token record if valid
    AND not expired, else None."""
    if not raw_secret:
        return None
    target = _hash_secret(raw_secret)
    now = int(time.time())
    for t in load_tokens():
        if not hmac.compare_digest(t.get("secret_hash", ""), target):
            continue
        if t.get("expires_ts") and now >= t["expires_ts"]:
            return None
        return t
    return None


def scope_allows(token_scope: str, required: str) -> bool:
    """admin > write > read. Higher scopes inherit lower permissions."""
    return _SCOPE_ORDER.get(token_scope, -1) >= _SCOPE_ORDER.get(required, 99)


# ── share-links (signed, no DB) ─────────────────────────────────────────


def make_share_link(scope: str = "read", ttl_s: int = 86400,
                    sub: str = "shared") -> str:
    """Generate a self-signed share token : base64(payload).hmac.

    Decoded by `verify_share_link`. No DB write — entirely stateless.
    """
    if scope not in _SCOPE_ORDER:
        raise ValueError(f"unknown scope {scope!r}")
    payload = {
        "scope": scope,
        "exp": int(time.time()) + int(ttl_s),
        "sub": sub,
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    secret = get_or_create_server_secret()
    sig = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"{body}.{sig_b64}"


def verify_share_link(token: str) -> Optional[dict]:
    """Validate a share-link. Returns payload if valid + not expired, else None."""
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    secret = get_or_create_server_secret()
    expected = base64.urlsafe_b64encode(
        hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
    ).decode("ascii").rstrip("=")
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        # Pad before decoding
        body_padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body_padded).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if int(payload.get("exp", 0)) <= int(time.time()):
        return None  # expired
    if payload.get("scope") not in _SCOPE_ORDER:
        return None
    return payload
