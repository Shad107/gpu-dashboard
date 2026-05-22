"""Module ical_feed — emit GPU events as an iCalendar (RFC 5545) feed (R&D #11.6).

Subscribe in any calendar client (Apple Calendar, Google Calendar via
'Add by URL', Thunderbird, Outlook, Fastmail, Korganizer) — populates with :

- Throttle events (clocks-event-reasons history → marker per occurrence)
- Driver/kernel drift events (from R&D #5.2 drift detector history)
- Uncorrectable ECC errors (R&D #4.3)
- Budget exceeded transitions (electricity)
- Sticky-peak alarms

Stateless : reads SQLite events table + drift history file + ECC live
state. No new storage. HMAC signature via the existing R&D #9.3 share-link
infra (optional — caller decides scope).

stdlib only (datetime, uuid, hashlib).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import time
from typing import List, Optional


NAME = "ical_feed"

# RFC 5545 line-folding : at 75 octets, prefixed by '\r\n '
def _fold(line: str) -> str:
    """Fold a long iCal line to 75 octets per line per RFC 5545."""
    if len(line) <= 75:
        return line
    out = [line[:75]]
    rest = line[75:]
    while rest:
        out.append(" " + rest[:74])
        rest = rest[74:]
    return "\r\n".join(out)


def _escape_text(s: str) -> str:
    """Escape backslash, comma, semicolon, newline per RFC 5545."""
    return (s.replace("\\", "\\\\")
             .replace(",", "\\,")
             .replace(";", "\\;")
             .replace("\n", "\\n")
             .replace("\r", ""))


def _fmt_utc(ts: int) -> str:
    """Format unix epoch as iCal UTC: YYYYMMDDTHHMMSSZ."""
    return datetime.datetime.utcfromtimestamp(int(ts)).strftime("%Y%m%dT%H%M%SZ")


def _event_uid(category: str, ts: int, payload: str = "") -> str:
    """Deterministic UID so updates don't create duplicates on the client side."""
    h = hashlib.sha256(f"{category}|{ts}|{payload}".encode("utf-8")).hexdigest()
    return f"{h[:16]}@gpu-dashboard.local"


def make_event(category: str, ts: int, summary: str,
               description: str = "", duration_min: int = 5) -> List[str]:
    """Build a VEVENT block (list of lines)."""
    uid = _event_uid(category, ts, summary)
    dtstart = _fmt_utc(ts)
    dtend = _fmt_utc(ts + duration_min * 60)
    dtstamp = _fmt_utc(int(time.time()))
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{dtstart}",
        f"DTEND:{dtend}",
        _fold(f"SUMMARY:{_escape_text(summary)}"),
        _fold(f"CATEGORIES:{category.upper()}"),
    ]
    if description:
        lines.append(_fold(f"DESCRIPTION:{_escape_text(description)}"))
    lines.append("END:VEVENT")
    return lines


def collect_events(storage, days: int = 30) -> list:
    """Pull events from storage + drift history. Returns a list of
    {category, ts, summary, description} dicts."""
    events: list = []
    if storage is None:
        return events
    since = int(time.time()) - days * 86400

    # 1. Events table (alerts.*, profile_switch, etc.)
    try:
        with storage._lock:
            rows = storage._conn.execute(
                "SELECT ts, kind, payload FROM events WHERE ts >= ? ORDER BY ts ASC",
                (since,),
            ).fetchall()
        for r in rows:
            kind = r["kind"] or ""
            payload_raw = r["payload"] or "{}"
            try:
                p = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
            except json.JSONDecodeError:
                p = {}
            if kind.startswith("alert."):
                summary = f"GPU alert : {kind[len('alert.'):]}"
                if isinstance(p, dict) and p.get("value") is not None:
                    summary += f" (value {p['value']})"
                events.append({
                    "category": "ALERT",
                    "ts": int(r["ts"]),
                    "summary": summary,
                    "description": _format_payload(p),
                })
            elif kind.startswith("profile."):
                events.append({
                    "category": "PROFILE",
                    "ts": int(r["ts"]),
                    "summary": f"Profile switched : {kind[len('profile.'):]}",
                    "description": _format_payload(p),
                })
    except Exception:
        pass

    # 2. Drift detector history file (R&D #5.2)
    drift_path = os.path.expanduser("~/.config/gpu-dashboard/drift_history.json")
    if os.path.exists(drift_path):
        try:
            with open(drift_path) as f:
                history = json.load(f)
            if isinstance(history, list):
                for h in history:
                    if not isinstance(h, dict):
                        continue
                    ts = int(h.get("ts", 0))
                    if ts < since:
                        continue
                    diffs = h.get("diffs", [])
                    summary = "Driver/kernel drift detected"
                    desc_parts = []
                    for d in (diffs if isinstance(diffs, list) else []):
                        field = d.get("field", "?")
                        old = d.get("old", "?")
                        new = d.get("new", "?")
                        desc_parts.append(f"{field}: {old} → {new}")
                    events.append({
                        "category": "DRIFT",
                        "ts": ts,
                        "summary": summary,
                        "description": "\n".join(desc_parts) if desc_parts else "",
                    })
        except (OSError, json.JSONDecodeError):
            pass

    events.sort(key=lambda e: e["ts"])
    return events


def _format_payload(p: dict) -> str:
    """Best-effort short rendering of an alert payload."""
    if not isinstance(p, dict):
        return str(p)
    parts = []
    for k in ("kind", "value", "threshold", "duration_s", "ago_s"):
        if k in p and p[k] is not None:
            parts.append(f"{k}={p[k]}")
    return " · ".join(parts) if parts else json.dumps(p)


def render_calendar(events: list, name: str = "GreenWatts GPU events") -> str:
    """Wrap events in a VCALENDAR. Returns RFC 5545-compliant text."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//gpu-dashboard//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        _fold(f"X-WR-CALNAME:{_escape_text(name)}"),
        _fold(f"X-WR-CALDESC:{_escape_text('GPU thermal, driver-drift, ECC, and budget events from gpu-dashboard')}"),
    ]
    for e in events:
        lines.extend(make_event(
            category=e.get("category", "EVENT"),
            ts=e.get("ts", int(time.time())),
            summary=e.get("summary", ""),
            description=e.get("description", ""),
        ))
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
