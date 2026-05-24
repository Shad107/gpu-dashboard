"""Module dynamic_debug_audit — kernel dyndbg call-site
inventory (R&D #85.1).

Reads /sys/kernel/debug/dynamic_debug/control — the kernel's
table of every ``pr_debug()`` / ``dev_dbg()`` call site
with its current enable mask.  Forgotten ``dyndbg=`` boot
args or runtime ``echo`` commands leak kernel log volume
and burn CPU on hot paths long after the debugging session
that turned them on.

Format (one site per line) :
  ``<file>:<lineno> [<module>] <function> =<flag-char>``

Flag characters :
  ``_``  off (default for most call sites)
  ``p``  print enabled
  ``f``  include function name
  ``l``  include line number
  ``m``  include module name
  ``t``  include thread id

Any site whose flags are not just ``_`` is considered
"enabled".

Verdicts (worst first) :

  many_dyndbg_sites_enabled    > 500 enabled call sites —
                               log spam and hot-path
                               printk overhead.
  some_dyndbg_sites_enabled    1 – 500 enabled sites
                               (informational — likely a
                               targeted debug session).
  ok                           0 enabled sites, control
                               file readable.
  requires_root                /sys/kernel/debug/dynamic_debug
                               unreadable (mode-700 debugfs).
  unknown                      debugfs absent or no
                               dynamic_debug subsystem.
"""
from __future__ import annotations

import os
from typing import Optional

DEFAULT_CONTROL = (
    "/sys/kernel/debug/dynamic_debug/control")
DEFAULT_DEBUGFS = "/sys/kernel/debug"

_ENABLED_FLAG_CHARS = set("pflmt")

# Thresholds
_MANY_SITES_FLOOR = 500


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_control(text: str) -> tuple[int, int]:
    """Returns (total_sites, enabled_sites).

    Each non-empty line is one call site ; the flag mask
    appears as ``=<chars>`` near the end of the line.
    """
    total = 0
    enabled = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        total += 1
        # Find rightmost "=<...>" token. The kernel format
        # consistently puts the flag near the end of the line
        # after the format string.
        idx = line.rfind("=")
        if idx < 0:
            continue
        tail = line[idx + 1:].split()
        if not tail:
            continue
        flags = tail[0]
        # "_" means "off" ; any of pflmt means enabled
        if any(c in _ENABLED_FLAG_CHARS for c in flags):
            enabled += 1
    return (total, enabled)


def read_control(control_path: str = DEFAULT_CONTROL,
                  debugfs: str = DEFAULT_DEBUGFS
                  ) -> tuple[Optional[str], str]:
    """Returns (text, state). state ∈ {ok, requires_root,
    unknown}."""
    text = _read_text(control_path)
    if text is not None:
        return (text, "ok")

    # Couldn't read. Distinguish requires_root from unknown.
    if not os.path.isdir(debugfs):
        return (None, "unknown")
    # debugfs dir exists ; try listing it.
    try:
        os.listdir(debugfs)
        debugfs_readable = True
    except (OSError, PermissionError):
        debugfs_readable = False
    if not debugfs_readable:
        return (None, "requires_root")
    # debugfs readable but no dynamic_debug subsystem
    if not os.path.exists(
            os.path.dirname(control_path)):
        return (None, "unknown")
    return (None, "requires_root")


def classify(state: str, total: int, enabled: int) -> dict:
    if state == "unknown":
        return {"verdict": "unknown",
                "reason": (
                    "/sys/kernel/debug/dynamic_debug "
                    "absent — kernel without "
                    "CONFIG_DYNAMIC_DEBUG or debugfs not "
                    "mounted.")}
    if state == "requires_root":
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/kernel/debug is mode-700 — "
                    "re-run dashboard as root for the "
                    "dyndbg call-site inventory.")}

    if enabled > _MANY_SITES_FLOOR:
        return {"verdict": "many_dyndbg_sites_enabled",
                "reason": (
                    f"{enabled} of {total} call sites have "
                    "dyndbg enabled — kernel log volume "
                    "and printk overhead."),
                "enabled": enabled, "total": total}

    if enabled > 0:
        return {"verdict": "some_dyndbg_sites_enabled",
                "reason": (
                    f"{enabled} of {total} call sites have "
                    "dyndbg enabled — likely a targeted "
                    "debug session."),
                "enabled": enabled, "total": total}

    return {"verdict": "ok",
            "reason": (
                f"{total} call sites tracked ; none "
                "enabled.")}


def status(config: Optional[dict] = None,
           control_path: str = DEFAULT_CONTROL,
           debugfs: str = DEFAULT_DEBUGFS) -> dict:
    text, state = read_control(control_path, debugfs)
    total, enabled = (
        parse_control(text) if text is not None else (0, 0))
    verdict = classify(state, total, enabled)
    return {
        "ok": verdict["verdict"] not in (
            "many_dyndbg_sites_enabled",
            "requires_root", "unknown"),
        "read_state": state,
        "total_sites": total,
        "enabled_sites": enabled,
        "verdict": verdict,
    }
