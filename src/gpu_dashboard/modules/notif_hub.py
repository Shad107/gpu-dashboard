"""Module notif_hub — unified notification fanout (R&D #6.1).

Single entry point that fans an alert out to any subset of channels :
Discord, Slack, Gotify, ntfy.sh, Pushover, generic JSON POST, SMTP.

stdlib only (urllib + smtplib). Channel definitions live in JSON config :
~/.config/gpu-dashboard/notif_channels.json with shape :

  {"channels": [
      {"id": "discord1", "type": "discord", "url": "https://discord.com/api/...",
       "name": "Main Discord", "enabled": true,
       "min_level": "warning",        # info | warning | critical
       "gpu_filter": null,            # int or null = any
       "quiet_hours": [22, 7]         # [start_h, end_h] or null
      },
      {"id": "phone", "type": "pushover",
       "token": "...", "user": "...",
       "name": "Push to phone", "enabled": true, "min_level": "critical"
      },
      ...
  ]}

Filter rules (all-AND) :
  - enabled must be true
  - notification level >= min_level
  - GPU index matches gpu_filter (or gpu_filter is null)
  - current hour outside quiet_hours window (if set)
"""
from __future__ import annotations

import datetime
import json
import os
import smtplib
import urllib.error
import urllib.request
from email.mime.text import MIMEText
from typing import Optional, Tuple


NAME = "notif_hub"

_LEVELS = {"info": 0, "warning": 1, "critical": 2}
_TIMEOUT = 5.0


def channels_path() -> str:
    return os.path.expanduser("~/.config/gpu-dashboard/notif_channels.json")


def load_channels() -> list:
    path = channels_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return []
        ch = data.get("channels", [])
        return ch if isinstance(ch, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_channels(channels: list) -> None:
    path = channels_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"channels": channels}, f, indent=2)


# ─── filter logic ─────────────────────────────────────────────────────────


def _quiet_now(quiet_hours, now_hour: int) -> bool:
    """Is `now_hour` inside the quiet window [start, end]?
    Window can wrap midnight (e.g. [22, 7] = 22:00 to 07:00)."""
    if not quiet_hours or not isinstance(quiet_hours, (list, tuple)) or len(quiet_hours) != 2:
        return False
    start, end = int(quiet_hours[0]) % 24, int(quiet_hours[1]) % 24
    if start == end:
        return False
    if start < end:
        return start <= now_hour < end
    # wraps midnight
    return now_hour >= start or now_hour < end


def channel_accepts(channel: dict, level: str, gpu_index: Optional[int] = None,
                    now_hour: Optional[int] = None) -> bool:
    """All-AND filter check."""
    if not channel.get("enabled", True):
        return False
    min_level = channel.get("min_level", "info")
    if _LEVELS.get(level, 0) < _LEVELS.get(min_level, 0):
        return False
    gpu_filter = channel.get("gpu_filter")
    if gpu_filter is not None and gpu_index is not None and int(gpu_filter) != int(gpu_index):
        return False
    if now_hour is None:
        now_hour = datetime.datetime.now().hour
    if _quiet_now(channel.get("quiet_hours"), now_hour):
        return False
    return True


# ─── adapters ─────────────────────────────────────────────────────────────


def _post_json(url: str, payload: dict, headers: Optional[dict] = None,
               timeout: float = _TIMEOUT) -> Tuple[bool, str]:
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ok = 200 <= r.status < 300
            return ok, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, f"net: {e.reason}"
    except (TimeoutError, OSError) as e:
        return False, f"err: {e}"


def send_discord(channel: dict, title: str, body: str) -> Tuple[bool, str]:
    url = channel.get("url")
    if not url:
        return False, "missing url"
    payload = {"content": f"**{title}**\n{body}"}
    return _post_json(url, payload)


def send_slack(channel: dict, title: str, body: str) -> Tuple[bool, str]:
    url = channel.get("url")
    if not url:
        return False, "missing url"
    payload = {"text": f"*{title}*\n{body}"}
    return _post_json(url, payload)


def send_gotify(channel: dict, title: str, body: str) -> Tuple[bool, str]:
    url = channel.get("url")
    token = channel.get("token")
    if not url or not token:
        return False, "missing url or token"
    full_url = url.rstrip("/") + f"/message?token={token}"
    return _post_json(full_url, {"title": title, "message": body})


def send_ntfy(channel: dict, title: str, body: str) -> Tuple[bool, str]:
    url = channel.get("url")  # full topic URL e.g. https://ntfy.sh/mytopic
    if not url:
        return False, "missing url"
    try:
        req = urllib.request.Request(
            url, data=body.encode("utf-8"),
            headers={"Title": title, "Content-Type": "text/plain"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            ok = 200 <= r.status < 300
            return ok, f"HTTP {r.status}"
    except (urllib.error.URLError, OSError) as e:
        return False, f"err: {e}"


def send_pushover(channel: dict, title: str, body: str) -> Tuple[bool, str]:
    token = channel.get("token")
    user = channel.get("user")
    if not token or not user:
        return False, "missing token or user"
    payload = {"token": token, "user": user, "title": title, "message": body}
    # Pushover wants form-encoded, not JSON
    try:
        import urllib.parse
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://api.pushover.net/1/messages.json", data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return 200 <= r.status < 300, f"HTTP {r.status}"
    except (urllib.error.URLError, OSError) as e:
        return False, f"err: {e}"


def send_generic_post(channel: dict, title: str, body: str) -> Tuple[bool, str]:
    url = channel.get("url")
    if not url:
        return False, "missing url"
    payload = {"title": title, "body": body, "source": "gpu-dashboard"}
    return _post_json(url, payload, headers=channel.get("headers"))


def send_smtp(channel: dict, title: str, body: str) -> Tuple[bool, str]:
    host = channel.get("host")
    port = int(channel.get("port", 587))
    user = channel.get("user")
    password = channel.get("password")
    sender = channel.get("from") or user
    to = channel.get("to")
    if not all([host, sender, to]):
        return False, "missing host/from/to"
    msg = MIMEText(body)
    msg["Subject"] = title
    msg["From"] = sender
    msg["To"] = to if isinstance(to, str) else ", ".join(to)
    try:
        if port == 465:
            srv = smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT)
        else:
            srv = smtplib.SMTP(host, port, timeout=_TIMEOUT)
            srv.starttls()
        if user and password:
            srv.login(user, password)
        srv.send_message(msg)
        srv.quit()
        return True, "sent"
    except (smtplib.SMTPException, OSError) as e:
        return False, f"smtp: {e}"


_ADAPTERS = {
    "discord":  send_discord,
    "slack":    send_slack,
    "gotify":   send_gotify,
    "ntfy":     send_ntfy,
    "pushover": send_pushover,
    "generic":  send_generic_post,
    "smtp":     send_smtp,
}


# ─── public API ───────────────────────────────────────────────────────────


def send(level: str, title: str, body: str,
         gpu_index: Optional[int] = None,
         channels: Optional[list] = None) -> list:
    """Fan-out send. Returns list of {channel_id, ok, msg} per channel attempted.

    `level` in {info, warning, critical}. Defaults : info.
    `channels` overrides load_channels() (useful for testing).
    """
    ch_list = channels if channels is not None else load_channels()
    now_hour = datetime.datetime.now().hour
    results: list = []
    for ch in ch_list:
        if not channel_accepts(ch, level, gpu_index, now_hour):
            continue
        adapter = _ADAPTERS.get(ch.get("type"))
        if adapter is None:
            results.append({"channel_id": ch.get("id"), "ok": False, "msg": f"unknown type {ch.get('type')}"})
            continue
        ok, msg = adapter(ch, title, body)
        results.append({"channel_id": ch.get("id"), "type": ch.get("type"), "ok": ok, "msg": msg})
    return results


def send_test(channel: dict) -> Tuple[bool, str]:
    """Fire a one-off test notification to a single channel (test-button)."""
    adapter = _ADAPTERS.get(channel.get("type"))
    if adapter is None:
        return False, f"unknown type {channel.get('type')}"
    return adapter(channel, "🧪 gpu-dashboard test",
                   "If you see this, the channel is wired correctly.")
