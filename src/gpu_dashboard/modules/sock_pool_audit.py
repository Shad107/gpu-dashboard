"""Module sock_pool_audit — socket pool + TIME_WAIT (R&D #50.4).

Parses /proc/net/sockstat{,6} for aggregate socket counts (TCP +
UDP + RAW inuse/orphan/tw, UDPLITE, FRAG memory) and counts
table rows in /proc/net/{tcp,tcp6,unix} for total socket
inventory.

Distinct from shipped #43.4 net_proto_counters (snmp/netstat-stats
counters) and #40.4 nic_queue_affinity (RX/TX queue) — this is
the *socket-pool / orphan / TIME_WAIT* dimension.

Verdicts (priority-ordered) :
  time_wait_high       /proc/net/sockstat TCP.tw >= 80 % of
                       tcp_max_tw_buckets (if readable) OR
                       absolute > 10 000 — too many sockets in
                       TIME_WAIT, kernel will start dropping new
                       outbound connections.
  orphan_high          TCP.orphan > 100 → forgotten sockets ;
                       memory leak risk.
  unix_backlog         /proc/net/unix > 5000 entries → daemon
                       leak (dbus, snap, browser ipc).
  ok                   all within normal ranges.
  unknown              /proc/net/sockstat unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "sock_pool_audit"


_PROC_NET_SOCKSTAT = "/proc/net/sockstat"
_PROC_NET_SOCKSTAT6 = "/proc/net/sockstat6"
_PROC_NET_TCP = "/proc/net/tcp"
_PROC_NET_TCP6 = "/proc/net/tcp6"
_PROC_NET_UNIX = "/proc/net/unix"
_PROC_SYS_TW_BUCKETS = "/proc/sys/net/ipv4/tcp_max_tw_buckets"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def parse_sockstat(text: Optional[str]) -> dict:
    """Lines like:
       sockets: used 1030
       TCP: inuse 19 orphan 0 tw 18 alloc 28 mem 290
       UDP: inuse 12 mem 441
       ...
    """
    out: dict = {}
    if not text:
        return out
    for line in text.splitlines():
        if ":" not in line:
            continue
        proto, _, rest = line.partition(":")
        proto = proto.strip()
        tokens = rest.split()
        section: dict = {}
        i = 0
        while i < len(tokens) - 1:
            try:
                section[tokens[i]] = int(tokens[i + 1])
                i += 2
            except ValueError:
                i += 1
        if section:
            out[proto] = section
    return out


def count_lines_minus_header(path: str) -> int:
    """Count non-header rows in /proc/net/{tcp,tcp6,unix}. These
    files have one header row; subtract 1 from total."""
    text = _read(path)
    if not text:
        return 0
    n = sum(1 for line in text.splitlines() if line.strip())
    return max(n - 1, 0)


_RECIPE_TW_HIGH = (
    "# Too many sockets in TIME_WAIT. Two short-term mitigations :\n"
    "#  1. Reduce TIME_WAIT timeout (default 60 s) — careful if you\n"
    "#     have NAT in front, this can break short-lived connections :\n"
    "echo 30 | sudo tee /proc/sys/net/ipv4/tcp_fin_timeout\n"
    "#  2. Bump the kernel cap so legitimate workloads aren't rejected :\n"
    "echo 524288 | sudo tee /proc/sys/net/ipv4/tcp_max_tw_buckets\n"
    "# Long-term : use connection pooling in the offending client."
)

_RECIPE_ORPHAN_HIGH = (
    "# Many TCP orphan sockets — sockets where the process holding\n"
    "# them died/closed but the kernel hasn't reclaimed them yet.\n"
    "# Often a sign of a leaky application :\n"
    "ss -t state close-wait\n"
    "# Or check per-process socket counts :\n"
    "for p in /proc/[0-9]*; do\n"
    "  n=$(ls $p/fd 2>/dev/null | xargs -I{} readlink $p/fd/{} 2>/dev/null | \\\n"
    "        grep -c socket)\n"
    "  [ \"$n\" -gt 100 ] && echo \"$p $(cat $p/comm 2>/dev/null) sockets=$n\"\n"
    "done | sort -k3 -n | tail -10"
)

_RECIPE_UNIX_BACKLOG = (
    "# /proc/net/unix has > 5000 entries — likely a daemon leak\n"
    "# (dbus, snap, browser IPC). Inspect top owners :\n"
    "ss -x -p | awk '{print $NF}' | sort | uniq -c | sort -nr | head"
)


_TW_RATIO_THRESHOLD = 0.80
_TW_ABSOLUTE_THRESHOLD = 10_000
_ORPHAN_THRESHOLD = 100
_UNIX_THRESHOLD = 5000


def classify(sockstat: dict, sockstat6: dict, tcp_count: int,
              tcp6_count: int, unix_count: int,
              tw_buckets_max: Optional[int]) -> dict:
    if not sockstat:
        return {"verdict": "unknown",
                "reason": "/proc/net/sockstat unreadable.",
                "recommendation": ""}
    tcp = sockstat.get("TCP", {})
    tw = tcp.get("tw", 0)
    orphan = tcp.get("orphan", 0)
    if (tw > 0 and tw_buckets_max
            and tw / tw_buckets_max >= _TW_RATIO_THRESHOLD) \
       or tw >= _TW_ABSOLUTE_THRESHOLD:
        return {"verdict": "time_wait_high",
                "reason": (f"TCP.tw={tw} "
                           + (f"({tw / tw_buckets_max:.0%} of "
                                f"tcp_max_tw_buckets="
                                f"{tw_buckets_max})"
                              if tw_buckets_max else "")
                           + " — kernel may start dropping new "
                           "outbound connections."),
                "recommendation": _RECIPE_TW_HIGH}
    if orphan >= _ORPHAN_THRESHOLD:
        return {"verdict": "orphan_high",
                "reason": (f"TCP.orphan={orphan} — sockets whose "
                           f"owning process closed but kernel "
                           f"hasn't reclaimed yet. Memory leak "
                           f"risk."),
                "recommendation": _RECIPE_ORPHAN_HIGH}
    if unix_count >= _UNIX_THRESHOLD:
        return {"verdict": "unix_backlog",
                "reason": (f"/proc/net/unix has {unix_count} "
                           f"entries — likely daemon IPC leak "
                           f"(dbus, snap, browser)."),
                "recommendation": _RECIPE_UNIX_BACKLOG}
    return {"verdict": "ok",
            "reason": (f"TCP inuse={tcp.get('inuse', 0)} "
                       f"orphan={orphan} tw={tw}, UDP "
                       f"inuse={sockstat.get('UDP', {}).get('inuse', 0)}, "
                       f"unix entries={unix_count}."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    sockstat = parse_sockstat(_read(_PROC_NET_SOCKSTAT))
    sockstat6 = parse_sockstat(_read(_PROC_NET_SOCKSTAT6))
    tcp_count = count_lines_minus_header(_PROC_NET_TCP)
    tcp6_count = count_lines_minus_header(_PROC_NET_TCP6)
    unix_count = count_lines_minus_header(_PROC_NET_UNIX)
    tw_buckets_max = _read_int(_PROC_SYS_TW_BUCKETS)
    verdict = classify(sockstat, sockstat6, tcp_count, tcp6_count,
                        unix_count, tw_buckets_max)
    return {
        "ok": bool(sockstat),
        "sockstat": sockstat,
        "sockstat6": sockstat6,
        "tcp_socket_count": tcp_count,
        "tcp6_socket_count": tcp6_count,
        "unix_socket_count": unix_count,
        "tcp_max_tw_buckets": tw_buckets_max,
        "verdict": verdict,
    }
