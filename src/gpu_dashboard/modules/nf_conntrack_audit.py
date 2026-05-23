"""Module nf_conntrack_audit — netfilter conntrack auditor (R&D #45.1).

Reads :
  /proc/sys/net/netfilter/nf_conntrack_{max,count,buckets,
    tcp_timeout_time_wait,generic_timeout}      configurable caps.
  /proc/net/stat/nf_conntrack                   per-CPU insert /
                                                drop counters
                                                (header + N rows).
  /proc/net/nf_conntrack                        active entries (we
                                                count, don't enumerate
                                                in JSON for size).

Verdicts (priority-ordered) :
  insert_drops              ≥1 CPU row's `insert_failed` or `drop`
                            counter > 0 since boot → kernel hit
                            nf_conntrack_max and silently dropped.
  table_saturated           current count ≥ 80 % of max.
  time_wait_bloat           TIME_WAIT timeout > 60 s + the live
                            count is ≥ 50 % of max (long timeout
                            + saturating count → likely TIME_WAIT
                            bloat under many short-lived
                            inference connections).
  ok                        count comfortably below 80 %, no insert
                            drops since boot.
  no_conntrack              /proc/sys/net/netfilter empty or
                            nf_conntrack_max unreadable.
  unknown                   /proc unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "nf_conntrack_audit"


_PROC_SYS_NETFILTER = "/proc/sys/net/netfilter"
_PROC_NET_STAT_NF = "/proc/net/stat/nf_conntrack"


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


_CT_FIELDS = (
    "nf_conntrack_max", "nf_conntrack_count", "nf_conntrack_buckets",
    "nf_conntrack_tcp_timeout_time_wait",
    "nf_conntrack_tcp_timeout_close_wait",
    "nf_conntrack_tcp_timeout_established",
    "nf_conntrack_generic_timeout",
)


def read_sysctls(sys_nf: str = _PROC_SYS_NETFILTER) -> dict:
    out: dict = {}
    if not os.path.isdir(sys_nf):
        return out
    for f in _CT_FIELDS:
        v = _read_int(os.path.join(sys_nf, f))
        if v is not None:
            out[f] = v
    return out


def parse_per_cpu_stats(text: str) -> dict:
    """First line is header (hex tokens), subsequent lines per-CPU
    rows of hex values. We sum across CPUs."""
    out: dict = {}
    if not text:
        return out
    lines = text.splitlines()
    if len(lines) < 2:
        return out
    header = lines[0].split()
    sums = [0] * len(header)
    for line in lines[1:]:
        parts = line.split()
        if len(parts) != len(header):
            continue
        for i, p in enumerate(parts):
            try:
                sums[i] += int(p, 16)
            except ValueError:
                continue
    for name, total in zip(header, sums):
        out[name] = total
    return out


_RECIPE_INSERT_DROPS = (
    "# conntrack table dropped inserts since boot — bump\n"
    "# nf_conntrack_max + buckets :\n"
    "echo 524288 | sudo tee /proc/sys/net/netfilter/nf_conntrack_max\n"
    "echo 524288 | sudo tee /proc/sys/net/netfilter/nf_conntrack_buckets\n"
    "# Persist :\n"
    "sudo tee /etc/sysctl.d/99-conntrack.conf <<'EOF'\n"
    "net.netfilter.nf_conntrack_max = 524288\n"
    "net.netfilter.nf_conntrack_buckets = 524288\n"
    "EOF"
)

_RECIPE_TIME_WAIT = (
    "# TIME_WAIT timeout default (120 s) is too long for a host\n"
    "# that fans out many short-lived inference connections.\n"
    "# Drop to 30 s :\n"
    "echo 30 | sudo tee \\\n"
    "  /proc/sys/net/netfilter/nf_conntrack_tcp_timeout_time_wait\n"
    "# Persist via /etc/sysctl.d/99-conntrack-twait.conf"
)


def classify(sysctls: dict, stats: dict) -> dict:
    if not sysctls and not stats:
        return {"verdict": "unknown",
                "reason": "/proc/sys/net/netfilter unreadable.",
                "recommendation": ""}
    if not sysctls.get("nf_conntrack_max"):
        return {"verdict": "no_conntrack",
                "reason": ("nf_conntrack module not loaded or its "
                           "sysctls not exposed. NAT / connection "
                           "tracking is disabled — fine for a host "
                           "that doesn't route or NAT."),
                "recommendation": ""}
    max_ct = sysctls.get("nf_conntrack_max", 0)
    count = sysctls.get("nf_conntrack_count", 0)
    inserts_failed = (stats.get("insert_failed", 0)
                       + stats.get("drop", 0))
    if inserts_failed > 0:
        return {"verdict": "insert_drops",
                "reason": (f"Conntrack table dropped "
                           f"{inserts_failed} inserts since boot "
                           f"(count={count}, max={max_ct}). Bump "
                           f"nf_conntrack_max + buckets."),
                "recommendation": _RECIPE_INSERT_DROPS}
    if max_ct > 0 and count >= max_ct * 0.8:
        pct = count / max_ct * 100
        return {"verdict": "table_saturated",
                "reason": (f"Conntrack table at {pct:.0f} % "
                           f"({count}/{max_ct}). Insert failures "
                           f"are imminent — bump now before the "
                           f"first drop."),
                "recommendation": _RECIPE_INSERT_DROPS}
    tw = sysctls.get("nf_conntrack_tcp_timeout_time_wait", 0)
    if (tw > 60 and max_ct > 0 and count >= max_ct * 0.5):
        return {"verdict": "time_wait_bloat",
                "reason": (f"TIME_WAIT timeout={tw}s + conntrack "
                           f"at {count / max_ct * 100:.0f} % of "
                           f"max. Likely TIME_WAIT bloat under "
                           f"many short-lived connections — drop "
                           f"the timeout."),
                "recommendation": _RECIPE_TIME_WAIT}
    return {"verdict": "ok",
            "reason": (f"Conntrack at {count}/{max_ct} "
                       f"({count / max(max_ct, 1) * 100:.1f} %) ; "
                       f"no insert drops, TIME_WAIT={tw}s."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    sysctls = read_sysctls(_PROC_SYS_NETFILTER)
    stat_text = _read(_PROC_NET_STAT_NF) or ""
    stats = parse_per_cpu_stats(stat_text)
    verdict = classify(sysctls, stats)
    return {
        "ok": bool(sysctls) or bool(stats),
        "sysctls": sysctls,
        "stats": stats,
        "verdict": verdict,
    }
