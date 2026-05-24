"""Module workqueue_cpumask_audit — kernel workqueue
cpumask / isolation posture (R&D #86.4).

On a 3090 box with ``isolcpus=4-11`` for an LLM worker,
kernel workqueues silently flooding the isolated cores
destroys p99 inference latency.  Nothing in stock
dashboards exposes the per-workqueue cpumask, so a
forgotten echo into /sys/devices/virtual/workqueue/cpumask
that overlaps isolcpus stays invisible until the user
manually checks.

Reads :

  /sys/devices/virtual/workqueue/cpumask              global mask
  /sys/devices/virtual/workqueue/cpumask_isolated     kernel-tracked isolated
  /sys/devices/virtual/workqueue/cpumask_requested    user-set request
  /sys/devices/virtual/workqueue/<wq>/cpumask         per-WQ allowed mask
  /sys/devices/virtual/workqueue/<wq>/max_active
  /sys/devices/virtual/workqueue/<wq>/nice
  /sys/devices/virtual/workqueue/<wq>/per_cpu         0 = unbound
  /sys/devices/system/cpu/isolated                    "1-3,5,…"

Verdicts (worst first) :

  wq_on_isolated_cpu      global or per-WQ cpumask
                          overlaps the host isolcpus list
                          — work runs on cores supposed to
                          be shielded.
  unbound_wq_default_only ≥3 unbound WQs have cpumask
                          equal to "1" (CPU 0 only) on a
                          ≥4-CPU box — concentration
                          bottleneck.
  nice_drift              ≥1 WQ has a nice value != 0 —
                          informational tweak.
  ok                      masks coherent with isolation
                          policy, no concentration.
  n/a                     /sys/devices/virtual/workqueue
                          absent.
  unknown                 present but cpumask unreadable.
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_WQ_ROOT = "/sys/devices/virtual/workqueue"
DEFAULT_CPU_ISOLATED = (
    "/sys/devices/system/cpu/isolated")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    s = _read_text(path)
    if s is None or s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _parse_cpumask_hex(text: Optional[str]) -> int:
    """Parse 'fff' or 'fff,00000fff' (multi-u32 big-end)
    into a Python int bitmask."""
    if not text:
        return 0
    text = text.replace(" ", "")
    # comma-separated u32 words, big-endian
    parts = text.split(",")
    out = 0
    for p in parts:
        try:
            out = (out << 32) | int(p, 16)
        except ValueError:
            continue
    return out


def _parse_cpu_list(text: Optional[str]) -> int:
    """Parse '1-3,5' into bitmask (bit N set if CPU N
    listed)."""
    if not text:
        return 0
    mask = 0
    for piece in text.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "-" in piece:
            try:
                lo, hi = piece.split("-", 1)
                for i in range(int(lo), int(hi) + 1):
                    mask |= (1 << i)
            except ValueError:
                continue
        else:
            try:
                mask |= (1 << int(piece))
            except ValueError:
                continue
    return mask


_NUM_CPUS_RE = re.compile(r"^processor\s*:\s*(\d+)", re.M)


def _cpu_count(cpuinfo: Optional[str]) -> int:
    if not cpuinfo:
        return 0
    return len(_NUM_CPUS_RE.findall(cpuinfo))


def list_workqueues(root: str = DEFAULT_WQ_ROOT
                     ) -> list[str]:
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return []
    out = []
    for name in entries:
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        # WQ entries always have a cpumask file
        if os.path.exists(os.path.join(d, "cpumask")):
            out.append(name)
    return out


def read_wq(root: str, name: str) -> dict:
    d = os.path.join(root, name)
    return {
        "name": name,
        "cpumask": _read_text(
            os.path.join(d, "cpumask")) or "",
        "max_active": _read_int(
            os.path.join(d, "max_active")),
        "nice": _read_int(os.path.join(d, "nice")),
        "per_cpu": _read_int(os.path.join(d, "per_cpu")),
    }


def classify(global_mask: int,
             isolated_mask: int,
             wqs: list[dict],
             wq_present: bool,
             cpu_count: int) -> dict:
    if not wq_present:
        return {"verdict": "n/a",
                "reason": (
                    "/sys/devices/virtual/workqueue absent "
                    "— no kernel workqueue sysfs surface.")}
    if not wqs:
        return {"verdict": "unknown",
                "reason": "Workqueue sysfs empty."}

    # 1. err — any WQ's cpumask overlaps isolated_mask
    if isolated_mask != 0:
        if global_mask & isolated_mask:
            return {
                "verdict": "wq_on_isolated_cpu",
                "reason": (
                    f"Global workqueue cpumask 0x"
                    f"{global_mask:x} overlaps isolated "
                    f"CPUs 0x{isolated_mask:x} — work "
                    "scheduled on shielded cores."),
                "global_mask": f"0x{global_mask:x}",
                "isolated_mask": f"0x{isolated_mask:x}"}
        for wq in wqs:
            mask = _parse_cpumask_hex(wq["cpumask"])
            if mask & isolated_mask:
                return {
                    "verdict": "wq_on_isolated_cpu",
                    "reason": (
                        f"Workqueue '{wq['name']}' "
                        f"cpumask 0x{mask:x} overlaps "
                        f"isolated 0x{isolated_mask:x}."),
                    "wq": wq["name"],
                    "mask": f"0x{mask:x}"}

    # 2. warn — unbound WQs all pinned to CPU 0
    if cpu_count >= 4:
        unbound = [wq for wq in wqs
                    if wq.get("per_cpu") == 0]
        single_cpu = [
            wq for wq in unbound
            if _parse_cpumask_hex(wq["cpumask"]) == 1]
        if len(single_cpu) >= 3:
            return {
                "verdict": "unbound_wq_default_only",
                "reason": (
                    f"{len(single_cpu)} unbound "
                    "workqueue(s) pinned to CPU 0 only — "
                    "concentration bottleneck."),
                "count": len(single_cpu),
                "wqs": [w["name"] for w in single_cpu]}

    # 3. accent — non-default nice value
    drifted_nice = [
        wq for wq in wqs if wq.get("nice", 0) not in (None, 0)]
    if drifted_nice:
        return {"verdict": "nice_drift",
                "reason": (
                    f"{len(drifted_nice)} workqueue(s) "
                    "have nice value != 0 — informational "
                    "tweak."),
                "wqs": [
                    f"{w['name']}={w['nice']}"
                    for w in drifted_nice]}

    return {"verdict": "ok",
            "reason": (
                f"{len(wqs)} workqueue(s) audited ; mask "
                f"0x{global_mask:x} coherent with isolation "
                f"0x{isolated_mask:x}.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_WQ_ROOT,
           isolated_path: str = DEFAULT_CPU_ISOLATED,
           cpuinfo_path: str = "/proc/cpuinfo") -> dict:
    wq_present = os.path.isdir(root)
    global_text = _read_text(os.path.join(root, "cpumask"))
    isolated_cpu_text = _read_text(isolated_path) or ""
    isolated_mask = _parse_cpu_list(isolated_cpu_text)
    global_mask = _parse_cpumask_hex(global_text)
    wq_names = list_workqueues(root) if wq_present else []
    wqs = [read_wq(root, n) for n in wq_names]
    cpuinfo = _read_text(cpuinfo_path)
    cpu_count = _cpu_count(cpuinfo)
    verdict = classify(global_mask, isolated_mask, wqs,
                        wq_present, cpu_count)
    return {
        "ok": verdict["verdict"] not in (
            "wq_on_isolated_cpu", "unknown"),
        "wq_count": len(wqs),
        "global_cpumask": (
            f"0x{global_mask:x}" if global_mask else ""),
        "isolated_cpus": isolated_cpu_text,
        "cpu_count": cpu_count,
        "verdict": verdict,
    }
