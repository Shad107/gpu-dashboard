"""Module xid_decoder — Xid kernel-error decoder (R&D #14.1).

NVRM Xid codes are NVIDIA's primary kernel-side error reporting mechanism.
They're cryptic numbers in `dmesg` / `journalctl -k` — this module parses
them and joins to a curated JSON dictionary for human cause + severity +
remediation hints.

Usage :
  events = decode_recent_journal(since="24h")
  → [{ts, code: 79, gpu: "PCI:0000:01:00", name: "GPU has fallen...",
       severity: "fail", remediation: "Reseat power cables..."}]

The dictionary at xid_codes.json is bundled (MIT) and covers ~17 of the
most commonly-encountered Xid codes. Unknown codes return {name: 'unknown',
severity: 'warn', remediation: 'consult NVIDIA Xid documentation'}.

stdlib only : subprocess + json + re.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Optional


NAME = "xid_decoder"

_DICT_PATH = os.path.join(os.path.dirname(__file__), "xid_codes.json")
_XID_REGEX = re.compile(
    r"NVRM:\s*Xid\s*\(PCI:([0-9a-f:.]+)\):\s*(\d+),?\s*(.*?)$",
    re.IGNORECASE | re.MULTILINE,
)


def load_dict() -> dict:
    """Read the bundled Xid dictionary."""
    try:
        with open(_DICT_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"codes": {}}


def decode(code: int) -> dict:
    """Look up a single Xid code. Returns the descriptor + fallback if unknown."""
    d = load_dict()
    entry = d.get("codes", {}).get(str(int(code)))
    if entry:
        return {
            "code": int(code),
            "name": entry.get("name", "?"),
            "cause": entry.get("cause", "?"),
            "severity": entry.get("severity", "warn"),
            "remediation": entry.get("remediation", "?"),
            "known": True,
        }
    return {
        "code": int(code),
        "name": "unknown Xid",
        "cause": "code not in bundled dictionary",
        "severity": "warn",
        "remediation": "consult NVIDIA Xid documentation, contribute to xid_codes.json",
        "known": False,
    }


def parse_log_lines(text: str) -> list:
    """Extract Xid events from a multi-line log string. Returns list of
    {gpu, code, summary} dicts (no timestamps — caller supplies them)."""
    events: list = []
    for m in _XID_REGEX.finditer(text or ""):
        gpu = m.group(1)
        try:
            code = int(m.group(2))
        except ValueError:
            continue
        summary = m.group(3).strip()
        events.append({"gpu": gpu, "code": code, "summary": summary})
    return events


def _run_journalctl(since: str = "24h", limit: int = 200) -> str:
    """Read kernel ring buffer for the last `since` time window."""
    # Convert "24h" / "30m" → "24 hours ago" / "30 minutes ago"
    m = re.match(r"^(\d+)([smhd])$", since)
    if not m:
        since = "24h"
        n, unit = "24", "h"
    else:
        n, unit = m.group(1), m.group(2)
    unit_map = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
    human_since = f"{n} {unit_map[unit]} ago"
    try:
        r = subprocess.run(
            ["journalctl", "-k", "--since", human_since, "--no-pager",
             "-o", "short-iso", "-n", str(int(limit))],
            capture_output=True, text=True, timeout=4,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return ""
    if r.returncode != 0:
        return ""
    return r.stdout


def decode_recent_journal(since: str = "24h", limit: int = 200) -> list:
    """Pull recent kernel log + decode any Xid events found."""
    text = _run_journalctl(since=since, limit=limit)
    if not text:
        return []
    out: list = []
    for line in text.splitlines():
        m = _XID_REGEX.search(line)
        if not m:
            continue
        # Try to extract ISO timestamp from the log line prefix
        ts_iso = line.split(" ", 1)[0] if " " in line else ""
        try:
            code = int(m.group(2))
        except ValueError:
            continue
        decoded = decode(code)
        decoded["gpu"] = m.group(1)
        decoded["summary"] = m.group(3).strip()
        decoded["ts_iso"] = ts_iso
        out.append(decoded)
    return out


def stats(events: Optional[list] = None) -> dict:
    """Aggregate stats for a list of decoded events. Useful for UI badge."""
    if events is None:
        events = decode_recent_journal(since="24h")
    counts = {"info": 0, "warn": 0, "fail": 0}
    for e in events:
        sev = e.get("severity", "warn")
        if sev in counts:
            counts[sev] += 1
    worst = "ok"
    if counts["fail"] > 0:
        worst = "fail"
    elif counts["warn"] > 0:
        worst = "warn"
    elif counts["info"] > 0:
        worst = "info"
    return {
        "ok": True,
        "available": True,
        "total_24h": len(events),
        "counts_by_severity": counts,
        "worst_severity": worst,
        "events": events[:50],   # cap for response size
    }
