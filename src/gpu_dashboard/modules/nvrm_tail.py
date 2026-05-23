"""Module nvrm_tail — Continuous NVRM / NvKmsKapi / GSP log tailer (R&D #28.7).

The shipped XID decoder (#14.x) and GSP-RM surfacer (#21.3) catch
*specific* failure events. But there's a third tier of NVIDIA kernel
chatter — RmInitAdapter completions, NvKmsKapi mode-set events,
GSP-RM watchdog pings — that's invisible to those modules. When
something goes wrong the lines are right there in dmesg ; they're
just buried.

This module pulls the last N hours of kernel-log NVIDIA lines via
`journalctl -k --since=...`, categorizes each into one of :

  rm_init      RmInit / RmInitAdapter
  nvkms        NvKmsKapi / nvidia-modeset
  gsp          GSP / GSP-RM
  xid          'Xid (' line  (cross-ref shipped decoder)
  nvrm_other   NVRM-prefixed lines not matching above
  driver_other other nvidia-* kernel messages

then returns the tail. Frontend re-polls for live view.

stdlib only.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Optional


NAME = "nvrm_tail"


_PATTERNS = [
    ("rm_init",      re.compile(r"NVRM.*Rm(?:Init|InitAdapter)", re.IGNORECASE)),
    ("xid",          re.compile(r"NVRM:.*Xid\s*\(", re.IGNORECASE)),
    ("gsp",          re.compile(r"GSP", re.IGNORECASE)),
    ("nvkms",        re.compile(r"(NvKmsKapi|nvidia-modeset)", re.IGNORECASE)),
    ("nvrm_other",   re.compile(r"NVRM:", re.IGNORECASE)),
    ("driver_other", re.compile(r"nvidia", re.IGNORECASE)),
]


def categorize(line: str) -> str:
    for label, pat in _PATTERNS:
        if pat.search(line):
            return label
    return "other"


# Parse a journalctl line of the form
#   "May 23 03:55:00 host kernel: NVRM: ..."
_LINE_HEAD_RE = re.compile(
    r"^(?P<ts>\w+\s+\d+\s+\d+:\d+:\d+)\s+\S+\s+kernel:\s+(?P<body>.*)$"
)


def parse_line(line: str) -> Optional[dict]:
    """Try to split timestamp + body. Falls back to body-only."""
    m = _LINE_HEAD_RE.match(line)
    if m:
        return {"ts": m.group("ts"), "body": m.group("body").strip()}
    s = line.strip()
    if not s:
        return None
    return {"ts": "", "body": s}


def run_journalctl(since: str = "1 hour ago",
                    limit: int = 200,
                    timeout: float = 4.0) -> Optional[list[str]]:
    """journalctl -k --since=<since> --no-pager  (limited to last N)."""
    if not shutil.which("journalctl"):
        return None
    try:
        r = subprocess.run(
            ["journalctl", "-k", f"--since={since}", "--no-pager",
             "-n", str(limit)],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.splitlines()


def filter_nvidia_lines(lines: list[str]) -> list[str]:
    """Keep only lines that mention NVRM / nvidia / GSP / NvKms."""
    out: list[str] = []
    for ln in lines:
        if ("NVRM" in ln or "nvidia" in ln.lower()
                or "GSP" in ln or "NvKms" in ln):
            out.append(ln)
    return out


def tail_categorized(since: str = "1 hour ago",
                       limit: int = 100) -> list[dict]:
    """Return categorized {category, ts, body} entries (most recent last)."""
    raw = run_journalctl(since=since, limit=2 * limit) or []
    nvidia_lines = filter_nvidia_lines(raw)
    out: list[dict] = []
    for ln in nvidia_lines[-limit:]:
        parsed = parse_line(ln)
        if parsed is None:
            continue
        out.append({"category": categorize(ln),
                     "ts": parsed["ts"],
                     "body": parsed["body"][:300]})
    return out


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    since = "1 hour ago"
    limit = 100
    if cfg:
        since = cfg.get("NVRM_TAIL_SINCE", since) or since
        try:
            limit = max(10, min(500, int(cfg.get("NVRM_TAIL_LIMIT", "100"))))
        except (ValueError, TypeError):
            pass
    if not shutil.which("journalctl"):
        return {"ok": False,
                "reason": "journalctl not available.",
                "entries": [],
                "category_counts": {}}
    entries = tail_categorized(since=since, limit=limit)
    cats: dict = {}
    for e in entries:
        cats[e["category"]] = cats.get(e["category"], 0) + 1
    return {
        "ok": True,
        "since": since,
        "entries": entries,
        "entry_count": len(entries),
        "category_counts": cats,
    }
