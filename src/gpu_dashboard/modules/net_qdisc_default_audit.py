"""Module net_qdisc_default_audit — default network qdisc
posture (R&D #101.2).

The kernel's default qdisc selection (pfifo_fast vs fq /
fq_codel / cake) decides whether bursty downloads (Steam, HF
model pulls, container image fetches) bufferbloat the link.
pfifo_fast has no fairness, no pacing — every flow stomps on
every other.

No existing module checks /proc/sys/net/core/default_qdisc.
tcp_congestion_control_audit covers TCP CC ; net_sysctl_audit
covers other net sysctls ; nic_queue_affinity reads queue
masks. bql_stall_counters_audit reads TX BQL stall.

Reads :

  /proc/sys/net/core/default_qdisc
  /proc/sys/net/core/netdev_budget
  /proc/sys/net/core/netdev_max_backlog
  /proc/sys/net/core/netdev_budget_usecs

Verdicts (worst-first) :

  pfifo_fast_default       err     default_qdisc=pfifo_fast —
                                   no fairness, classic
                                   bufferbloat trap.
  netdev_budget_low        warn    netdev_budget < 300 —
                                   softirq net loop runs too
                                   few packets per pass,
                                   adds latency under load.
  noqueue_default          accent  default_qdisc=noqueue —
                                   non-standard, intentional
                                   on virtual hosts, weird
                                   on a desktop.
  ok                               fq / fq_codel / cake +
                                   healthy budget.
  requires_root                    sysctls unreadable.
  unknown                          /proc/sys/net/core absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "net_qdisc_default_audit"

DEFAULT_NET_CORE = "/proc/sys/net/core"

_HEALTHY_QDISCS = ("fq", "fq_codel", "cake")
_BUDGET_MIN = 300


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_str(path: str) -> Optional[str]:
    t = _read_text(path)
    return t.strip() if t is not None else None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def classify(net_present: bool,
             default_qdisc: Optional[str],
             netdev_budget: Optional[int]) -> dict:
    if not net_present:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/sys/net/core absent — no "
                    "network namespace exposed.")}
    if default_qdisc is None:
        return {"verdict": "requires_root",
                "reason": (
                    "net.core sysctls unreadable — "
                    "re-run as root.")}

    # err — pfifo_fast (no fairness)
    if default_qdisc == "pfifo_fast":
        return {
            "verdict": "pfifo_fast_default",
            "reason": (
                "default_qdisc=pfifo_fast — no flow "
                "fairness, no pacing. Steam / HF model "
                "downloads will bufferbloat the uplink "
                "and stall everything else.")}

    # warn — netdev_budget below default
    if (netdev_budget is not None
            and netdev_budget < _BUDGET_MIN):
        return {
            "verdict": "netdev_budget_low",
            "reason": (
                f"netdev_budget={netdev_budget} (< "
                f"{_BUDGET_MIN} default). Net softirq "
                "loop runs too few packets per pass, "
                "adds latency under load.")}

    # accent — noqueue (virtual / odd)
    if default_qdisc == "noqueue":
        return {
            "verdict": "noqueue_default",
            "reason": (
                "default_qdisc=noqueue — non-standard "
                "for a desktop / homelab. Common on VMs "
                "with virtio-net, weird on bare metal.")}

    if default_qdisc not in _HEALTHY_QDISCS:
        return {"verdict": "ok",
                "reason": (
                    f"default_qdisc={default_qdisc} "
                    f"(non-default but not flagged). "
                    f"netdev_budget={netdev_budget}.")}

    return {"verdict": "ok",
            "reason": (
                f"default_qdisc={default_qdisc} ; "
                f"netdev_budget={netdev_budget}. Healthy.")}


def status(config: Optional[dict] = None,
           net_core: str = DEFAULT_NET_CORE) -> dict:
    net_present = os.path.isdir(net_core)
    default_qdisc = (
        _read_str(os.path.join(net_core, "default_qdisc"))
        if net_present else None)
    netdev_budget = (
        _read_int(os.path.join(net_core, "netdev_budget"))
        if net_present else None)
    netdev_max_backlog = (
        _read_int(os.path.join(net_core,
                                "netdev_max_backlog"))
        if net_present else None)
    budget_usecs = (
        _read_int(os.path.join(net_core,
                                "netdev_budget_usecs"))
        if net_present else None)
    verdict = classify(net_present, default_qdisc,
                       netdev_budget)
    return {
        "ok": verdict["verdict"] == "ok",
        "default_qdisc": default_qdisc,
        "netdev_budget": netdev_budget,
        "netdev_max_backlog": netdev_max_backlog,
        "netdev_budget_usecs": budget_usecs,
        "verdict": verdict,
    }
