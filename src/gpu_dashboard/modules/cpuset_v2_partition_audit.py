"""Module cpuset_v2_partition_audit — cgroup v2 cpuset
partition + effective-mask drift detector (R&D #96.2).

Existing cgroup modules don't touch cpuset :

  * cgroup_root_audit, cgroup_pids_controller_audit,
    cgroup_memcap, cgroup_memevents_audit,
    cgroup_v2_memory_peak_audit, cgroup_io_stat_audit
  * proc_task_affinity_audit reads /proc/<pid>/status masks

This audit walks /sys/fs/cgroup/**/cpuset.* files for the
v2 partition / effective-mask state. Catches the "container
asked for CPU 0-7 but boot isolated 0-7, so cpuset.cpus.
effective is empty and tasks run on whatever the parent
allows" trap.

Reads :

  /sys/fs/cgroup/cgroup.controllers
  /sys/fs/cgroup/**/cpuset.cpus
  /sys/fs/cgroup/**/cpuset.cpus.effective
  /sys/fs/cgroup/**/cpuset.cpus.partition

Verdicts (worst-first) :

  cpuset_effective_empty   err   any cgroup that requested a
                                 non-empty cpus mask but
                                 cpuset.cpus.effective is
                                 empty — tasks have no CPU.
  partition_invalid        warn  any cgroup's
                                 cpuset.cpus.partition
                                 contains "invalid" (root
                                 invalid / member invalid).
  cpuset_drift             accent any cgroup where requested
                                 cpus != effective cpus
                                 (hotplug / isolation
                                 stripped some).
  cpuset_sane              ok    all coherent OR no
                                 non-default requests.
  requires_root            tree unreadable.
  unknown                  no cpuset controller (cgroup v1
                           setup).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "cpuset_v2_partition_audit"

DEFAULT_CGROUP_ROOT = "/sys/fs/cgroup"

# Bound the walk to keep audits fast even on hosts with
# thousands of transient systemd scopes.
_MAX_CGROUPS_WALKED = 5000


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def parse_cpu_list(text: Optional[str]) -> set:
    """Parse '0-3,8,10-11' style mask. Returns set of int."""
    if not text:
        return set()
    out: set = set()
    for tok in text.strip().split(","):
        tok = tok.strip()
        if not tok:
            continue
        if "-" in tok:
            try:
                lo, hi = tok.split("-", 1)
                lo_i, hi_i = int(lo), int(hi)
            except ValueError:
                continue
            if lo_i <= hi_i:
                out.update(range(lo_i, hi_i + 1))
        else:
            try:
                out.add(int(tok))
            except ValueError:
                continue
    return out


def controller_present(root: str) -> Optional[bool]:
    text = _read_text(os.path.join(root, "cgroup.controllers"))
    if text is None:
        return None
    return "cpuset" in text.split()


def walk_cgroups(root: str,
                 max_visit: int = _MAX_CGROUPS_WALKED
                 ) -> list:
    """Walk the cgroup tree, return list of dicts only for
    cgroups with a NON-DEFAULT cpuset request."""
    out: list = []
    visited = 0
    for dirpath, _, files in os.walk(root):
        visited += 1
        if visited > max_visit:
            break
        if "cpuset.cpus" not in files:
            continue
        requested_raw = _read_text(
            os.path.join(dirpath, "cpuset.cpus"))
        # An empty cpuset.cpus means "inherit from parent" —
        # default state ; nothing to audit.
        if not requested_raw or not requested_raw.strip():
            continue
        effective_raw = _read_text(
            os.path.join(dirpath, "cpuset.cpus.effective"))
        partition = _read_text(
            os.path.join(dirpath, "cpuset.cpus.partition"))
        out.append({
            "path": os.path.relpath(dirpath, root),
            "requested": parse_cpu_list(requested_raw),
            "effective": parse_cpu_list(effective_raw),
            "partition": (partition or "").strip(),
        })
    return out


def classify(present: Optional[bool],
             cgroups: list) -> dict:
    if present is None:
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/fs/cgroup/cgroup.controllers "
                    "unreadable — re-run as root.")}
    if not present:
        return {"verdict": "unknown",
                "reason": (
                    "cpuset controller absent from "
                    "cgroup.controllers — cgroup v1 host or "
                    "kernel built without CONFIG_CPUSETS.")}
    if not cgroups:
        return {"verdict": "cpuset_sane",
                "reason": (
                    "No cgroup has a non-default cpuset "
                    "request — all inherit from root.")}

    # err — effective mask empty
    stranded = [c for c in cgroups
                if c["requested"] and not c["effective"]]
    if stranded:
        names = [c["path"] for c in stranded]
        return {
            "verdict": "cpuset_effective_empty",
            "reason": (
                f"{len(stranded)} cgroup(s) with non-empty "
                f"cpuset.cpus but empty cpus.effective: "
                f"{names[:3]}. Tasks are stranded — no CPUs "
                "available.")}

    # warn — partition state invalid
    invalid = [c for c in cgroups
               if "invalid" in c["partition"].lower()]
    if invalid:
        names = [c["path"] for c in invalid]
        return {
            "verdict": "partition_invalid",
            "reason": (
                f"{len(invalid)} cgroup(s) with partition "
                f"state containing 'invalid': {names[:3]}. "
                "Partition request failed — kernel rejected "
                "the topology.")}

    # accent — requested != effective
    drifted = [c for c in cgroups
               if c["requested"] != c["effective"]]
    if drifted:
        names = [c["path"] for c in drifted]
        return {
            "verdict": "cpuset_drift",
            "reason": (
                f"{len(drifted)} cgroup(s) where requested "
                f"cpus != effective cpus (e.g. {names[:3]}). "
                "Likely cpu hotplug or isolcpus took some "
                "CPUs away.")}

    return {"verdict": "cpuset_sane",
            "reason": (
                f"{len(cgroups)} cgroup(s) with non-default "
                "cpuset requests ; all effective = requested "
                "and no invalid partitions.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_CGROUP_ROOT) -> dict:
    present = controller_present(root)
    cgroups = walk_cgroups(root) if present else []
    verdict = classify(present, cgroups)
    return {
        "ok": verdict["verdict"] == "cpuset_sane",
        "non_default_count": len(cgroups),
        "verdict": verdict,
    }
