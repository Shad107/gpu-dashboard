"""Module webhook — generic HTTP POST notifications.

Lightweight alternative to Telegram. POSTs JSON to a configured URL. Compatible
out-of-the-box with :
- Discord webhook URLs           (uses {"content": text})
- Slack incoming webhooks         (uses {"text": text})
- n8n / Home Assistant / generic  (uses {"text", "kind", "source", "timestamp"})

Auto-detects Discord/Slack by URL pattern and adapts the payload shape.

Public API:
- send(url, text, kind="info") → (ok: bool, msg: str)
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Optional, Tuple


NAME = "webhook"

_DEFAULT_TIMEOUT = 4.0


def _payload_for_url(url: str, text: str, kind: str) -> dict:
    """Pick the right JSON shape based on URL pattern.

    Discord  : {"content": text}
    Slack    : {"text": text}
    Generic  : {"text": text, "kind": kind, "source": "gpu-dashboard", ...}
    """
    if "discord.com/api/webhooks/" in url or "discordapp.com/api/webhooks/" in url:
        return {"content": text}
    if "hooks.slack.com/" in url:
        return {"text": text}
    return {
        "text": text,
        "kind": kind,
        "source": "gpu-dashboard",
        "timestamp": int(time.time()),
    }


def send(url: str, text: str, kind: str = "info", timeout: float = _DEFAULT_TIMEOUT) -> Tuple[bool, str]:
    """POST JSON to `url`. Returns (ok, message-or-error)."""
    if not url:
        return False, "no url"

    payload = _payload_for_url(url, text, kind)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "gpu-dashboard/0.3"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            # Discord returns 204 No Content on success.
            if 200 <= r.status < 300:
                return True, f"HTTP {r.status}"
            return False, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, f"connection error: {e}"
