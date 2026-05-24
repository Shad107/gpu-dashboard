"""Module unix_socket_inventory_audit — /proc/net/unix
breakdown by namespace + state (R&D #85.3).

Existing sock_pool_audit catches the total-count case at
> 5000 entries (the daemon-IPC-leak floor).  This audit goes
one layer deeper :

  abstract sockets   path starts with ``@`` or is empty —
                     no on-disk anchor.  Long-running ML
                     toolchains (Jupyter, Ray, CUDA MPS,
                     Triton, dbus) leak these and they can
                     pile up without showing in any /tmp
                     listing.
  named sockets      path starts with ``/`` — visible on
                     the filesystem.
  unnamed            empty path, typically inherited via
                     socketpair() across fork.

The signature of an ML-stack leak is hundreds of abstract
sockets despite a healthy total, predicting ENOBUFS / FD-
exhaustion before the system actually runs out.

Verdicts (worst first) :

  unix_socket_leak       total > 2000 AND abstract > 500
                         — clear toolchain / IPC leak.
  unix_socket_growth     abstract > 200 (growth signal
                         even if total is moderate).
  many_unix_sockets      total > 500 (informational —
                         busy / daemon-heavy box).
  ok                     total below 500.
  unknown                /proc/net/unix unreadable.
"""
from __future__ import annotations

import os
from typing import Optional

DEFAULT_UNIX_FILE = "/proc/net/unix"

# Thresholds
_LEAK_TOTAL_FLOOR = 2000
_LEAK_ABSTRACT_FLOOR = 500
_GROWTH_ABSTRACT_FLOOR = 200
_BUSY_TOTAL_FLOOR = 500


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_unix(text: str) -> dict:
    """Returns counts {total, abstract, named, unnamed,
    listening}.

    /proc/net/unix columns :
      Num RefCount Protocol Flags Type St Inode Path
    Header line is skipped.  Path may be missing entirely
    (unnamed socket).
    """
    total = 0
    abstract = 0
    named = 0
    unnamed = 0
    listening = 0
    for i, line in enumerate(text.splitlines()):
        if i == 0:  # header
            continue
        parts = line.split()
        if len(parts) < 7:
            continue
        total += 1
        try:
            state = int(parts[5], 16)
        except ValueError:
            state = 0
        # SS_UNCONNECTED = 1 in user-visible form = listening
        if state == 1:
            listening += 1
        # Path is the 8th column ; absent if line has only 7
        # whitespace-separated tokens.
        if len(parts) >= 8:
            path = parts[7]
            if path.startswith("@"):
                abstract += 1
            elif path.startswith("/"):
                named += 1
            else:
                # Unusual path format — count as named to be
                # conservative.
                named += 1
        else:
            unnamed += 1
    return {
        "total": total,
        "abstract": abstract,
        "named": named,
        "unnamed": unnamed,
        "listening": listening,
    }


def classify(counts: Optional[dict]) -> dict:
    if counts is None:
        return {"verdict": "unknown",
                "reason": "/proc/net/unix unreadable."}

    total = counts["total"]
    abstract = counts["abstract"]
    listening = counts["listening"]

    if (total > _LEAK_TOTAL_FLOOR
            and abstract > _LEAK_ABSTRACT_FLOOR):
        return {"verdict": "unix_socket_leak",
                "reason": (
                    f"{total} unix sockets, {abstract} "
                    "abstract — toolchain / daemon IPC "
                    "leak signature."),
                "total": total, "abstract": abstract}

    if abstract > _GROWTH_ABSTRACT_FLOOR:
        return {"verdict": "unix_socket_growth",
                "reason": (
                    f"{abstract} abstract sockets — growth "
                    "signal even if total is moderate."),
                "abstract": abstract}

    if total > _BUSY_TOTAL_FLOOR:
        return {"verdict": "many_unix_sockets",
                "reason": (
                    f"{total} unix sockets, "
                    f"{listening} listening — busy host."),
                "total": total, "listening": listening}

    return {"verdict": "ok",
            "reason": (
                f"{total} unix sockets ({abstract} "
                f"abstract, {listening} listening).")}


def status(config: Optional[dict] = None,
           path: str = DEFAULT_UNIX_FILE) -> dict:
    text = _read_text(path)
    counts = parse_unix(text) if text is not None else None
    verdict = classify(counts)
    return {
        "ok": verdict["verdict"] not in (
            "unix_socket_leak", "unknown"),
        "total": (counts["total"] if counts else 0),
        "abstract": (counts["abstract"] if counts else 0),
        "named": (counts["named"] if counts else 0),
        "unnamed": (counts["unnamed"] if counts else 0),
        "listening": (counts["listening"] if counts else 0),
        "verdict": verdict,
    }
