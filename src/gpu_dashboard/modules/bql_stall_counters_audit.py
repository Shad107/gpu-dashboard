"""Module bql_stall_counters_audit — per-TX-queue BQL stall
counter posture (R&D #100.2).

The kernel's Byte Queue Limits (BQL) infrastructure exposes
three rarely-noticed counters per TX queue :

  /sys/class/net/<dev>/queues/tx-N/byte_queue_limits/
    stall_cnt   # number of times the queue stalled
    stall_max   # longest single stall (ms)
    stall_thrs  # detection threshold (ms ; 0 = disabled)

On bursty desktop workloads (LAN model pulls, container
networking spikes), these are the only kernel-visible signal
that a driver-side TX hang briefly wedged the link. Existing
modules cover :

  nic_queue_affinity      → byte_queue_limits/limit + rps/xps
  softnet_stat_audit      → /proc/net/softnet_stat (host-wide)

Neither reads the stall_* family.

Reads :

  /sys/class/net/<dev>/queues/tx-*/byte_queue_limits/stall_cnt
                                                   /stall_max
                                                   /stall_thrs

Skips lo, virtual bridges (docker0, virbr*, br-*, veth*) — the
"primary NIC" interpretation only makes sense for physical
or virtio-like devices.

Verdicts (worst-first) :

  bql_stall_max_above_5s   err     any tx-queue saw a single
                                   stall >= 5000 ms — link
                                   wedged at least once.
  bql_stall_cnt_growing    warn    stall_cnt > 0 on the
                                   primary NIC — recurring TX
                                   starvation.
  bql_stall_thrs_disabled  accent  stall_thrs == 0 on every
                                   queue — detection is off,
                                   future stalls invisible.
  ok                                counters clean, threshold
                                    enabled.
  requires_root                     BQL nodes unreadable.
  unknown                           no physical NIC found.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "bql_stall_counters_audit"

DEFAULT_NET_SYSFS = "/sys/class/net"

_STALL_MAX_ERR_MS = 5000
_VIRTUAL_PREFIXES = (
    "lo", "docker", "virbr", "br-", "veth", "tap", "tun")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def is_physical(iface: str) -> bool:
    """Filter out loopback / docker / virbr / veth / etc."""
    for pref in _VIRTUAL_PREFIXES:
        if iface == pref or iface.startswith(pref):
            return False
    return True


def walk_iface_queues(iface_dir: str) -> list:
    """Return list of dicts per TX queue with stall counters."""
    out: list = []
    queues_dir = os.path.join(iface_dir, "queues")
    if not os.path.isdir(queues_dir):
        return out
    try:
        entries = os.listdir(queues_dir)
    except OSError:
        return out
    for q in sorted(entries):
        if not q.startswith("tx-"):
            continue
        bql = os.path.join(queues_dir, q,
                            "byte_queue_limits")
        if not os.path.isdir(bql):
            continue
        out.append({
            "queue": q,
            "stall_cnt": _read_int(
                os.path.join(bql, "stall_cnt")),
            "stall_max": _read_int(
                os.path.join(bql, "stall_max")),
            "stall_thrs": _read_int(
                os.path.join(bql, "stall_thrs")),
        })
    return out


def walk_all(net_sysfs: str = DEFAULT_NET_SYSFS) -> list:
    """Return list of dicts {iface, queues:[...]}"""
    out: list = []
    if not os.path.isdir(net_sysfs):
        return out
    try:
        ifaces = sorted(os.listdir(net_sysfs))
    except OSError:
        return out
    for iface in ifaces:
        if not is_physical(iface):
            continue
        d = os.path.join(net_sysfs, iface)
        queues = walk_iface_queues(d)
        if not queues:
            continue
        out.append({"iface": iface, "queues": queues})
    return out


def classify(net_present: bool,
             ifaces: list,
             readable: bool) -> dict:
    if not net_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/class/net absent — no network "
                    "namespace exposed.")}
    if not readable:
        return {"verdict": "requires_root",
                "reason": (
                    "BQL nodes unreadable — re-run as "
                    "root.")}
    if not ifaces:
        return {"verdict": "unknown",
                "reason": (
                    "No physical interfaces with BQL "
                    "queues found.")}

    # err — any queue saw stall_max >= 5s
    big_stalls = []
    for ent in ifaces:
        for q in ent["queues"]:
            sm = q["stall_max"]
            if sm is not None and sm >= _STALL_MAX_ERR_MS:
                big_stalls.append(
                    (ent["iface"], q["queue"], sm))
    if big_stalls:
        sample = big_stalls[0]
        return {
            "verdict": "bql_stall_max_above_5s",
            "reason": (
                f"{len(big_stalls)} TX queue(s) saw a "
                f">= {_STALL_MAX_ERR_MS} ms stall "
                f"(example: {sample[0]}/{sample[1]}="
                f"{sample[2]} ms). Link was wedged.")}

    # warn — primary NIC has stall_cnt > 0
    primary = ifaces[0]
    growing = [
        q for q in primary["queues"]
        if q["stall_cnt"] and q["stall_cnt"] > 0]
    if growing:
        names = [
            f"{q['queue']}={q['stall_cnt']}"
            for q in growing[:5]]
        return {
            "verdict": "bql_stall_cnt_growing",
            "reason": (
                f"{primary['iface']} has {len(growing)} "
                f"TX queue(s) with non-zero stall_cnt: "
                f"{names}. Recurring TX starvation.")}

    # accent — stall_thrs = 0 on every queue
    all_thrs_zero = True
    for ent in ifaces:
        for q in ent["queues"]:
            if q["stall_thrs"] and q["stall_thrs"] > 0:
                all_thrs_zero = False
                break
        if not all_thrs_zero:
            break
    if all_thrs_zero:
        return {
            "verdict": "bql_stall_thrs_disabled",
            "reason": (
                "stall_thrs=0 on every TX queue — BQL "
                "stall detection is off. Future driver "
                "TX hangs will be invisible.")}

    return {"verdict": "ok",
            "reason": (
                f"{len(ifaces)} iface(s) checked ; BQL "
                "counters clean, threshold enabled.")}


def status(config: Optional[dict] = None,
           net_sysfs: str = DEFAULT_NET_SYSFS) -> dict:
    net_present = os.path.isdir(net_sysfs)
    readable = (net_present
                and os.access(net_sysfs, os.R_OK))
    ifaces = walk_all(net_sysfs) if readable else []
    verdict = classify(net_present, ifaces, readable)
    queue_count = sum(len(e["queues"]) for e in ifaces)
    return {
        "ok": verdict["verdict"] == "ok",
        "iface_count": len(ifaces),
        "queue_count": queue_count,
        "verdict": verdict,
    }
