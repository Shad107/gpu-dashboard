"""Module journal_audit — systemd journald config + storage (R&D #48.4).

Parses /etc/systemd/journald.conf{,.d/*.conf} for the
[Journal]-section settings that affect rate-limit drops + storage
usage. Sums file sizes under /var/log/journal/ for the persistent-
storage footprint.

Stays *out* of binary journal parsing — that's a 100+ line struct
unpacking exercise. Config + storage sizing alone catches the
most-common foot-guns.

  Storage=                 'auto' (default) / 'persistent' /
                           'volatile' / 'none'. Storage=none means
                           journalctl shows nothing — dashboard
                           diagnostics half-blind.
  RateLimitIntervalSec=    rate-limit window (default 30 s).
  RateLimitBurst=          messages per window (default 10000).
                           Low values silently drop application
                           logs.
  SystemMaxUse=            total cap on persistent storage
                           (default 10 % of FS, max 4 GiB).
  SystemKeepFree=          minimum free space to maintain
                           (default 15 %).
  SystemMaxFileSize=       per-journal-file cap (default ~ 1/8 of
                           SystemMaxUse).
  SystemMaxFiles=          max archived files (default 100).

Verdicts (priority-ordered) :
  storage_disabled         Storage=none → diagnostics blind.
  rate_limit_risky         RateLimitBurst < 100 OR
                           RateLimitIntervalSec > 60 → likely
                           dropping application logs.
  oversized                /var/log/journal/ > 4 GiB AND
                           SystemMaxUse not explicitly set →
                           default-grew-unbounded pattern.
  no_persistent_storage    /var/log/journal/ empty or absent →
                           Storage=auto fell back to volatile
                           (RAM only) because /var/log/journal
                           doesn't exist.
  ok                       reasonable defaults + storage under
                           4 GiB OR SystemMaxUse explicitly set.
  unknown                  /etc/systemd unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "journal_audit"


_JOURNALD_CONF = "/etc/systemd/journald.conf"
_JOURNALD_CONF_D = "/etc/systemd/journald.conf.d"
_VAR_LOG_JOURNAL = "/var/log/journal"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


_KEY_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9]*)\s*=\s*(.*)$")


def parse_journald_conf(text: Optional[str]) -> dict:
    """Parse [Journal] section key=value lines. Skip comments + ini
    section headers. Last value wins (matches systemd semantics)."""
    out: dict = {}
    if not text:
        return out
    in_journal = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_journal = (stripped.lower() == "[journal]")
            continue
        if not in_journal:
            continue
        m = _KEY_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def merge_conf(conf_main: str, conf_d_dir: str) -> dict:
    """Main file + every drop-in in conf.d/*.conf, last write wins."""
    merged: dict = {}
    merged.update(parse_journald_conf(_read(conf_main)))
    if os.path.isdir(conf_d_dir):
        try:
            names = sorted(os.listdir(conf_d_dir))
        except OSError:
            names = []
        for n in names:
            if not n.endswith(".conf"):
                continue
            merged.update(parse_journald_conf(
                _read(os.path.join(conf_d_dir, n))))
    return merged


def dir_size_bytes(path: str) -> int:
    if not os.path.isdir(path):
        return 0
    total = 0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def parse_size_value(s: Optional[str]) -> Optional[int]:
    """systemd accepts suffixes K, M, G, T (with optional B).
    Returns bytes or None on garbage."""
    if not s:
        return None
    m = re.match(r"^\s*(\d+)\s*([KMGT]?)i?B?\s*$", s,
                  re.IGNORECASE)
    if not m:
        return None
    n = int(m.group(1))
    suffix = m.group(2).upper()
    mults = {"": 1, "K": 1024, "M": 1024**2,
              "G": 1024**3, "T": 1024**4}
    return n * mults.get(suffix, 1)


_OVERSIZED_THRESHOLD = 4 * 1024**3      # 4 GiB
_RATE_LIMIT_BURST_MIN = 100
_RATE_LIMIT_INTERVAL_MAX_SEC = 60


_RECIPE_STORAGE_DISABLED = (
    "# Storage=none → journald discards every record. Re-enable :\n"
    "sudo systemctl edit systemd-journald\n"
    "# [Service] section, add :\n"
    "#   [Service]\n"
    "# Or override drop-in :\n"
    "sudo tee /etc/systemd/journald.conf.d/99-storage.conf <<'EOF'\n"
    "[Journal]\n"
    "Storage=auto\n"
    "EOF\n"
    "sudo systemctl restart systemd-journald"
)

_RECIPE_RATE_LIMIT = (
    "# RateLimitBurst is too low — application logs likely being\n"
    "# dropped. Bump :\n"
    "sudo tee /etc/systemd/journald.conf.d/99-rate-limit.conf <<'EOF'\n"
    "[Journal]\n"
    "RateLimitIntervalSec=30s\n"
    "RateLimitBurst=10000\n"
    "EOF\n"
    "sudo systemctl restart systemd-journald"
)

_RECIPE_OVERSIZED = (
    "# /var/log/journal exceeds 4 GiB. Cap explicitly :\n"
    "sudo tee /etc/systemd/journald.conf.d/99-size.conf <<'EOF'\n"
    "[Journal]\n"
    "SystemMaxUse=2G\n"
    "EOF\n"
    "sudo systemctl restart systemd-journald\n"
    "# Then trim down existing archives :\n"
    "sudo journalctl --vacuum-size=2G"
)

_RECIPE_NO_PERSISTENT = (
    "# /var/log/journal/ empty — journald is running in volatile\n"
    "# (RAM-only) mode. Persist :\n"
    "sudo mkdir -p /var/log/journal\n"
    "sudo systemctl restart systemd-journald\n"
    "# Verify : journalctl --disk-usage"
)


def _parse_interval_sec(s: Optional[str]) -> Optional[int]:
    """systemd interval : '30s' / '5min' / '1h'. Returns seconds."""
    if not s:
        return None
    m = re.match(
        r"^\s*(\d+)\s*(s|sec|min|h|hour)?\s*$", s, re.IGNORECASE)
    if not m:
        return None
    n = int(m.group(1))
    unit = (m.group(2) or "s").lower()
    if unit in ("s", "sec"):
        return n
    if unit in ("min",):
        return n * 60
    if unit in ("h", "hour"):
        return n * 3600
    return n


def classify(conf: dict, storage_bytes: int,
              persistent_dir_exists: bool) -> dict:
    if not conf and not persistent_dir_exists:
        return {"verdict": "unknown",
                "reason": ("/etc/systemd/journald.conf + /var/log/"
                           "journal both absent."),
                "recommendation": ""}
    storage = (conf.get("Storage") or "").strip().lower()
    if storage == "none":
        return {"verdict": "storage_disabled",
                "reason": "Storage=none — journald discards records.",
                "recommendation": _RECIPE_STORAGE_DISABLED}
    burst_str = conf.get("RateLimitBurst")
    interval_str = conf.get("RateLimitIntervalSec")
    burst: Optional[int] = None
    if burst_str:
        try:
            burst = int(burst_str)
        except ValueError:
            burst = None
    interval_sec = _parse_interval_sec(interval_str)
    if (burst is not None and 0 < burst < _RATE_LIMIT_BURST_MIN) or \
       (interval_sec is not None
            and interval_sec > _RATE_LIMIT_INTERVAL_MAX_SEC):
        return {"verdict": "rate_limit_risky",
                "reason": (f"RateLimitBurst={burst_str or '?'}, "
                           f"RateLimitIntervalSec={interval_str or '?'} "
                           f"— application logs likely dropped."),
                "recommendation": _RECIPE_RATE_LIMIT}
    max_use_set = bool(conf.get("SystemMaxUse"))
    if storage_bytes >= _OVERSIZED_THRESHOLD and not max_use_set:
        gib = storage_bytes / (1024**3)
        return {"verdict": "oversized",
                "reason": (f"/var/log/journal = {gib:.1f} GiB and "
                           f"SystemMaxUse not set — unbounded growth."),
                "recommendation": _RECIPE_OVERSIZED}
    if persistent_dir_exists and storage_bytes == 0:
        return {"verdict": "no_persistent_storage",
                "reason": ("/var/log/journal/ empty — journald is "
                           "running in volatile (RAM-only) mode."),
                "recommendation": _RECIPE_NO_PERSISTENT}
    return {"verdict": "ok",
            "reason": (f"Storage={storage or 'auto'}, "
                       f"persistent={storage_bytes / (1024**3):.2f} GiB, "
                       f"rate-limit burst={burst_str or 'default'}."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir("/etc/systemd"):
        return {
            "ok": False,
            "verdict": {"verdict": "unknown",
                         "reason": "/etc/systemd unreadable.",
                         "recommendation": ""},
            "config": {}, "journal_bytes": 0,
        }
    conf = merge_conf(_JOURNALD_CONF, _JOURNALD_CONF_D)
    persistent_exists = os.path.isdir(_VAR_LOG_JOURNAL)
    storage_bytes = (dir_size_bytes(_VAR_LOG_JOURNAL)
                       if persistent_exists else 0)
    verdict = classify(conf, storage_bytes, persistent_exists)
    return {
        "ok": True,
        "config": conf,
        "journal_bytes": storage_bytes,
        "journal_gib": round(storage_bytes / (1024**3), 2),
        "persistent_dir_exists": persistent_exists,
        "verdict": verdict,
    }
