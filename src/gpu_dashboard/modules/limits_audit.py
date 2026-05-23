"""Module limits_audit — PAM limits memlock auditor (bench-class).

Shipped #29.8 rlimit_audit only sees systemd-launched daemons — it
reads /proc/<pid>/limits and emits a per-unit systemd Drop-In. But
when a user runs `llama-server` directly in an SSH session, a tmux
pane, or via sudo, the limits come from PAM (/etc/security/
limits.conf + /etc/security/limits.d/*.conf), not systemd. If PAM
doesn't set `memlock` to unlimited, the session's mlock cap stays
at whatever systemd-pam set on login (typically 8 MiB on modern
Debian/Ubuntu) — silently swapping the GGUF mmap.

This module parses both files, isolates memlock rules, and emits:

  unlimited       any `* hard memlock unlimited` rule → ok
  explicit_high   memlock >= 1 GiB explicitly set → ok
  explicit_low    memlock set but < 1 GiB → warn
  default         no memlock rules at all → relies on systemd-pam
                  default ; surface the 99-llm.conf recipe
  unknown         can't read limits.conf or limits.d/

Hard limits win over soft (the hard limit is the user's max,
including for mlock).

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "limits_audit"


_LIMITS_ROOT = "/etc/security"


INFINITY = 2 ** 63 - 1


_LIMIT_RE = re.compile(
    r"^\s*(\S+)\s+(hard|soft|-)\s+(\S+)\s+(\S+)\s*$"
)


def parse_limits_line(line: str) -> Optional[dict]:
    if not line:
        return None
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    m = _LIMIT_RE.match(s)
    if not m:
        return None
    return {
        "domain": m.group(1),
        "type": m.group(2),
        "item": m.group(3),
        "value": m.group(4),
    }


def parse_limits_file(text: str) -> list:
    if not text:
        return []
    out: list = []
    for line in text.splitlines():
        rec = parse_limits_line(line)
        if rec:
            out.append(rec)
    return out


def collect_memlock_rules(root: str = _LIMITS_ROOT) -> list:
    out: list = []
    main = os.path.join(root, "limits.conf")
    if os.path.exists(main):
        try:
            with open(main) as f:
                rules = parse_limits_file(f.read())
            for r in rules:
                if r["item"] == "memlock":
                    out.append(r)
        except OSError:
            pass
    d = os.path.join(root, "limits.d")
    try:
        entries = sorted(os.listdir(d))
    except OSError:
        entries = []
    for name in entries:
        if not name.endswith(".conf"):
            continue
        try:
            with open(os.path.join(d, name)) as f:
                rules = parse_limits_file(f.read())
        except OSError:
            continue
        for r in rules:
            if r["item"] == "memlock":
                out.append(r)
    return out


def value_to_bytes(v: str) -> Optional[int]:
    if v is None:
        return None
    s = v.strip().lower()
    if s in ("unlimited", "infinity"):
        return INFINITY
    try:
        return int(s) * 1024  # PAM memlock is in KiB
    except ValueError:
        return None


_HIGH_THRESHOLD = 1024 * 1024 * 1024   # 1 GiB


_RECIPE = (
    "# Drop a /etc/security/limits.d/99-llm.conf:\n"
    "sudo tee /etc/security/limits.d/99-llm.conf <<'EOF'\n"
    "# Allow unlimited mlock for all users (LLM rig)\n"
    "*    hard    memlock    unlimited\n"
    "*    soft    memlock    unlimited\n"
    "EOF\n"
    "# New SSH sessions / tmux panes will inherit the new cap.\n"
    "# Existing sessions stay capped — log out + back in.\n"
    "# Companion: #29.8 rlimit_audit covers the systemd path."
)


def classify(rules: list) -> dict:
    if not rules:
        return {"verdict": "default",
                "reason": ("No `memlock` rules in /etc/security/limits.conf "
                           "or limits.d/*.conf. Non-systemd sessions "
                           "(SSH, tmux, sudo) inherit the systemd-pam "
                           "default (typically 8 MiB) — `--mlock` will "
                           "silently fail."),
                "recommendation": _RECIPE}
    # Only rules that apply universally count for the global cap.
    # Group-scoped rules (e.g. @pipewire) don't affect llama-server
    # unless the user happens to be in that group.
    universal = [r for r in rules if r["domain"] in ("*", "root")]
    if not universal:
        return {"verdict": "default",
                "reason": ("memlock rules exist but only target specific "
                           "groups/users (e.g. @pipewire). The general "
                           "user running llama-server inherits the "
                           "systemd-pam default — typically 8 MiB."),
                "recommendation": _RECIPE}
    best_bytes = -1
    for r in universal:
        if r["type"] != "hard" and r["type"] != "-":
            continue
        b = value_to_bytes(r["value"])
        if b is None:
            continue
        if b > best_bytes:
            best_bytes = b
    if best_bytes >= INFINITY:
        return {"verdict": "unlimited",
                "reason": ("PAM memlock is `unlimited` — `--mlock` from "
                           "non-systemd sessions will work."),
                "recommendation": ""}
    if best_bytes >= _HIGH_THRESHOLD:
        gib = best_bytes / 1024 ** 3
        return {"verdict": "explicit_high",
                "reason": (f"PAM memlock cap is {gib:.1f} GiB — "
                           f"high enough for typical LLM models."),
                "recommendation": ""}
    if best_bytes > 0:
        mib = best_bytes / 1024 ** 2
        return {"verdict": "explicit_low",
                "reason": (f"PAM memlock cap is {mib:.0f} MiB — too low "
                           f"for LLM model files ; mlock will fail "
                           f"silently for models above this size."),
                "recommendation": _RECIPE}
    return {"verdict": "default",
            "reason": "memlock rules present but no parseable value.",
            "recommendation": _RECIPE}


def status(cfg=None) -> dict:
    if not os.path.isdir(_LIMITS_ROOT):
        return {"ok": False, "error": "limits_unavailable",
                "reason": f"{_LIMITS_ROOT} not present."}
    rules = collect_memlock_rules(_LIMITS_ROOT)
    files: list = []
    if os.path.exists(os.path.join(_LIMITS_ROOT, "limits.conf")):
        files.append("limits.conf")
    d = os.path.join(_LIMITS_ROOT, "limits.d")
    try:
        for n in sorted(os.listdir(d)):
            if n.endswith(".conf"):
                files.append(f"limits.d/{n}")
    except OSError:
        pass
    verdict = classify(rules)
    return {
        "ok": True,
        "files": files,
        "memlock_rules": rules,
        "verdict": verdict,
    }
