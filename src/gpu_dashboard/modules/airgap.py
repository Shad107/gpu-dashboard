"""Module airgap — central network gate for air-gapped deployments (R&D #12.7).

Some users (defense labs, banking, compliance-heavy R&D) cannot allow
*any* outbound traffic from their LLM rig. Today the dashboard makes
opportunistic calls to :

  - huggingface.co/api/models/<repo>   (R&D #10.3 model card lookup)
  - github.com/Shad107/gpu-dashboard   (update check)
  - api.telegram.org                   (Telegram alerts)
  - discord.com / slack.com / ntfy.sh  (notif hub adapters)
  - https?://<user-configured-host>    (generic webhooks, smart-plug)

This module provides a SINGLE policy + audit layer.

Usage from a callsite that does urllib.request.urlopen :

    from gpu_dashboard.modules import airgap
    cfg = ctx["config"]
    if not airgap.allow_url(cfg, url):
        return  # blocked, already audited
    # ... proceed with urlopen ...

Or for opt-in safety, wrap urlopen :

    response = airgap.safe_urlopen(cfg, url, timeout=4)
    if response is None:
        return  # blocked or failed

In AIRGAP_MODE=1, every non-loopback URL is BLOCKED and appended to a
bounded in-memory audit buffer. /api/airgap/audit exposes the last
N attempts for compliance review.

Loopback addresses (127.0.0.1, ::1, localhost, plus any RFC1918 host
when AIRGAP_LAN_ALLOWED=1) are always permitted — these are needed
for the dashboard itself (UPS NUT, llama-server, Qdrant, etc.).

stdlib only.
"""
from __future__ import annotations

import collections
import re
import threading
import time
import urllib.parse
from typing import Optional


NAME = "airgap"

_AUDIT_BUFFER_SIZE = 200
_lock = threading.Lock()
_audit: collections.deque = collections.deque(maxlen=_AUDIT_BUFFER_SIZE)

# Loopback patterns (always allowed)
_LOOPBACK_PATTERNS = (
    "127.0.0.1", "::1", "localhost", "0.0.0.0",
)

# RFC1918 patterns for LAN-allowed mode
_RFC1918 = re.compile(
    r"^(10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|169\.254\.)"
)


def is_enabled(cfg) -> bool:
    if cfg is None:
        return False
    return str(cfg.get("AIRGAP_MODE", "0")).lower() in ("1", "true", "yes")


def lan_allowed(cfg) -> bool:
    if cfg is None:
        return False
    return str(cfg.get("AIRGAP_LAN_ALLOWED", "0")).lower() in ("1", "true", "yes")


def _classify_host(host: str) -> str:
    """Return one of : loopback | lan | external."""
    if not host:
        return "external"
    h = host.lower().strip("[]")  # strip ipv6 brackets
    if h in _LOOPBACK_PATTERNS or h.endswith(".localhost"):
        return "loopback"
    if _RFC1918.match(h):
        return "lan"
    return "external"


def record_block(url: str, reason: str = "airgap-mode") -> None:
    """Append a blocked attempt to the audit buffer."""
    entry = {
        "ts": int(time.time()),
        "url": url[:512],
        "reason": reason,
    }
    with _lock:
        _audit.append(entry)


def allow_url(cfg, url: str) -> bool:
    """Policy check. Returns True if the URL is allowed, False if blocked
    (and records the attempt in the audit buffer)."""
    if not is_enabled(cfg):
        return True
    try:
        parsed = urllib.parse.urlparse(url)
    except (ValueError, AttributeError):
        record_block(url or "?", reason="malformed-url")
        return False
    host = parsed.hostname or ""
    klass = _classify_host(host)
    if klass == "loopback":
        return True
    if klass == "lan" and lan_allowed(cfg):
        return True
    record_block(url, reason=f"airgap-blocked-{klass}")
    return False


def safe_urlopen(cfg, url: str, *args, **kwargs):
    """Drop-in replacement for urllib.request.urlopen that respects the
    air-gap policy. Returns None when blocked OR when the underlying
    urlopen raises an exception (matches the project's silent-fail style)."""
    if not allow_url(cfg, url):
        return None
    import urllib.request
    import urllib.error
    try:
        return urllib.request.urlopen(url, *args, **kwargs)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return None


def get_audit(limit: int = 100) -> list:
    """Return the most recent blocked attempts (newest first)."""
    with _lock:
        items = list(_audit)
    items.reverse()  # newest first
    return items[:max(0, min(limit, _AUDIT_BUFFER_SIZE))]


def clear_audit() -> int:
    """Reset the audit buffer. Returns count cleared."""
    with _lock:
        n = len(_audit)
        _audit.clear()
    return n


def status(cfg) -> dict:
    """Top-level air-gap status snapshot."""
    return {
        "enabled": is_enabled(cfg),
        "lan_allowed": lan_allowed(cfg),
        "audit_buffer_size": _AUDIT_BUFFER_SIZE,
        "blocked_count_24h": _count_recent(86400),
        "blocked_count_total": len(_audit),
    }


def _count_recent(window_s: int) -> int:
    """Count blocked entries newer than `window_s` ago."""
    cutoff = int(time.time()) - window_s
    with _lock:
        return sum(1 for e in _audit if int(e.get("ts", 0)) >= cutoff)
