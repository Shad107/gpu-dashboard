"""Module cgroup_delegate_audit — slice delegation +
freeze/zombie detector (R&D #97.3).

cgroup_root_audit reads /sys/fs/cgroup/cgroup.subtree_control
on the v2 ROOT only. None of the other cgroup-controller-
specific audits (cgroup_io_stat_audit, cgroup_memevents_audit,
cgroup_v2_memory_peak_audit, cgroup_pids_controller_audit,
cpuset_v2_partition_audit) walk the slice tree for
delegation / freeze / populated drift.

This audit walks the well-known systemd slices and the
per-user manager (user@<uid>.service) and looks for :

  * subtree_control on a slice that lacks the io controller
    — workloads inside cannot have IO accounting.
  * cgroup.freeze = 1 unexpectedly — a child is frozen and
    can't run.
  * cgroup.events says populated=1 but cgroup.procs is
    empty — zombie cgroup (race or leak).
  * /sys/kernel/cgroup/delegate missing — pre-v5.13 kernel,
    no rootless delegation interface.

Reads :

  /sys/fs/cgroup/cgroup.controllers          (probe v2)
  /sys/fs/cgroup/system.slice/cgroup.{subtree_control,
                                        freeze, events,
                                        procs}
  /sys/fs/cgroup/user.slice/...../user@<uid>.service/
                                        cgroup.subtree_control
  /sys/kernel/cgroup/delegate

Verdicts (worst-first) :

  frozen_descendant            err   any walked cgroup has
                                     cgroup.freeze = 1 — a
                                     workload is suspended
                                     and won't run.
  populated_but_no_procs       warn  cgroup.events says
                                     populated but procs is
                                     empty — zombie state.
  subtree_missing_io           accent system.slice or
                                     user@<uid>.service
                                     subtree_control lacks
                                     io — workloads inside
                                     can't have IO accounting.
  delegate_file_missing        accent /sys/kernel/cgroup/
                                     delegate absent — pre-
                                     v5.13 kernel, rootless
                                     delegation needs root.
  ok                           coherent.
  requires_root                tree unreadable.
  unknown                      cgroup v2 not mounted.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "cgroup_delegate_audit"

DEFAULT_CGROUP_ROOT = "/sys/fs/cgroup"
DEFAULT_DELEGATE_FILE = "/sys/kernel/cgroup/delegate"

# Slices to inspect. user@<uid>.service is discovered
# dynamically under user.slice/user-<uid>.slice/.
_SLICES_TO_CHECK = ("system.slice",)


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


def _parse_events(text: Optional[str]) -> dict:
    """cgroup.events format: 'populated 0\\nfrozen 0\\n'"""
    out: dict = {}
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                continue
    return out


def find_user_manager_paths(root: str) -> list:
    """Find user@<uid>.service paths under user.slice."""
    out: list = []
    user_slice = os.path.join(root, "user.slice")
    if not os.path.isdir(user_slice):
        return out
    try:
        for d1 in os.listdir(user_slice):
            if not d1.startswith("user-") or not d1.endswith(
                    ".slice"):
                continue
            user_d = os.path.join(user_slice, d1)
            for d2 in os.listdir(user_d):
                if (d2.startswith("user@")
                        and d2.endswith(".service")):
                    out.append(os.path.join(user_d, d2))
    except OSError:
        pass
    return out


def walk_slices(root: str = DEFAULT_CGROUP_ROOT) -> list:
    """Return list of dicts for each interesting slice."""
    out: list = []
    paths = [os.path.join(root, s) for s in _SLICES_TO_CHECK]
    paths.extend(find_user_manager_paths(root))
    for path in paths:
        if not os.path.isdir(path):
            continue
        subtree_text = _read_text(
            os.path.join(path, "cgroup.subtree_control"))
        controllers = (subtree_text or "").split()
        freeze = _read_int(
            os.path.join(path, "cgroup.freeze"))
        events = _parse_events(
            _read_text(os.path.join(path, "cgroup.events")))
        procs_text = _read_text(
            os.path.join(path, "cgroup.procs"))
        proc_count = (
            len([ln for ln in procs_text.splitlines()
                 if ln.strip()])
            if procs_text else 0)
        child_count = 0
        try:
            for entry in os.listdir(path):
                if entry.startswith("cgroup."):
                    continue
                if os.path.isfile(os.path.join(
                        path, entry, "cgroup.events")):
                    child_count += 1
        except OSError:
            pass
        out.append({
            "path": os.path.relpath(path, root),
            "controllers": controllers,
            "freeze": freeze,
            "events": events,
            "proc_count": proc_count,
            "child_count": child_count,
        })
    return out


def classify(v2_present: bool,
             v2_unreadable: bool,
             slices: list,
             delegate_present: bool) -> dict:
    if not v2_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/fs/cgroup/cgroup.controllers "
                    "absent — cgroup v2 not mounted.")}
    if v2_unreadable:
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/fs/cgroup/* unreadable — re-run "
                    "as root.")}

    # err — any walked slice frozen unexpectedly
    frozen = [s for s in slices if s["freeze"] == 1]
    if frozen:
        names = [s["path"] for s in frozen]
        return {
            "verdict": "frozen_descendant",
            "reason": (
                f"{len(frozen)} slice(s) have "
                f"cgroup.freeze=1: {names}. Workloads inside "
                "are suspended.")}

    # warn — populated but procs empty AND no child cgroups
    # (populated=1 on a parent with child cgroups is normal —
    #  procs live in scope/service subcgroups)
    zombies = [
        s for s in slices
        if s["events"].get("populated") == 1
        and s["proc_count"] == 0
        and s.get("child_count", 0) == 0]
    if zombies:
        names = [s["path"] for s in zombies]
        return {
            "verdict": "populated_but_no_procs",
            "reason": (
                f"{len(zombies)} slice(s) report "
                f"populated=1 but cgroup.procs is empty: "
                f"{names}. Zombie cgroup state — possibly "
                "racy or leaked sub-cgroup.")}

    # accent — missing io in systemd slices
    missing_io = [
        s for s in slices
        if "io" not in s["controllers"]]
    if missing_io:
        names = [s["path"] for s in missing_io]
        return {
            "verdict": "subtree_missing_io",
            "reason": (
                f"{len(missing_io)} slice(s) lack the io "
                f"controller in subtree_control: {names}. "
                "Workloads inside (incl. user-slice services) "
                "won't have IO accounting / throttling. "
                "Default for systemd ; bump if needed.")}

    # accent — delegate file missing
    if not delegate_present:
        return {
            "verdict": "delegate_file_missing",
            "reason": (
                "/sys/kernel/cgroup/delegate absent — pre-"
                "v5.13 kernel. Rootless delegation needs "
                "root + chown to set up.")}

    return {"verdict": "ok",
            "reason": (
                f"{len(slices)} slice(s) inspected ; "
                "delegate file present, all coherent.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_CGROUP_ROOT,
           delegate_file: str = DEFAULT_DELEGATE_FILE) -> dict:
    v2_present = os.path.isfile(
        os.path.join(root, "cgroup.controllers"))
    v2_unreadable = False
    slices: list = []
    if v2_present:
        try:
            slices = walk_slices(root)
        except (OSError, PermissionError):
            v2_unreadable = True
    delegate_present = os.path.isfile(delegate_file)
    verdict = classify(v2_present, v2_unreadable, slices,
                       delegate_present)
    return {
        "ok": verdict["verdict"] == "ok",
        "slice_count": len(slices),
        "delegate_present": delegate_present,
        "verdict": verdict,
    }
