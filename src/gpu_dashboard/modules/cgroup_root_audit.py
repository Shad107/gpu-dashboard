"""Module cgroup_root_audit — v2 root delegation state (R&D #58.1).

Distinct from existing :
  * cgroup_memevents_audit (#50.2) — leaf memory.events.
  * cgcpuio / cgmemcap (older modules) — per-unit knobs.
This module reads the v2 root state and surfaces delegation
problems that make every other cgroup metric meaningless.

Why this matters :

* If cgroup.subtree_control on the v2 root lists fewer controllers
  than cgroup.controllers, systemd slices created for llama-server
  silently inherit *no quota* — `memory.events`, PSI accounting,
  and `memory.max` enforcement become noise.
* Hybrid v1/v2 mounts (legacy /sys/fs/cgroup/cpu vs new v2)
  fragment accounting across two hierarchies. Modern distros
  default to unified v2 ; if you see v1 named controllers under
  /sys/fs/cgroup/<name>/, something is unmigrated.
* Deep nesting (> 5 levels) in the user-slice path is a sign of
  a misconfigured user-session manager — descends into
  cgroup.events / freeze / kill APIs slowly.

Reads :
  /sys/fs/cgroup/cgroup.controllers
  /sys/fs/cgroup/cgroup.subtree_control
  /sys/fs/cgroup/cgroup.stat
  /sys/fs/cgroup/cgroup.max.{depth, descendants}
  /sys/fs/cgroup/init.scope/cgroup.{type, procs, events}
  /proc/self/cgroup

Verdicts (priority-ordered) :
  hybrid_v1_v2                 v1 named controllers present
                               under /sys/fs/cgroup/.
  missing_controllers          cgroup.controllers ⊃
                               cgroup.subtree_control — some
                               controllers undelegated.
  deep_nesting                 own cgroup path depth > 5.
  ok                           v2-only, controllers fully
                               delegated, nesting reasonable.
  unknown                      /sys/fs/cgroup absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional, Set


NAME = "cgroup_root_audit"


_SYS_CGROUP = "/sys/fs/cgroup"
_PROC_SELF_CGROUP = "/proc/self/cgroup"


# v1 named controllers that would appear as subdirs at the root
# under a hybrid mount.
_V1_CONTROLLERS = {
    "cpu", "cpuacct", "cpuset", "memory", "io", "blkio",
    "pids", "devices", "freezer", "net_cls", "net_prio",
    "perf_event", "rdma", "hugetlb", "misc",
}


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


def _read_tokens(p: str) -> Set[str]:
    t = _read(p)
    if t is None:
        return set()
    return set(t.split())


def detect_hybrid(sys_cgroup: str = _SYS_CGROUP) -> List[str]:
    """Return v1-controller dir names found under the cgroup root."""
    if not os.path.isdir(sys_cgroup):
        return []
    out: List[str] = []
    for name in os.listdir(sys_cgroup):
        if name in _V1_CONTROLLERS and \
                os.path.isdir(os.path.join(sys_cgroup, name)):
            out.append(name)
    return sorted(out)


def parse_self_cgroup(text: Optional[str]) -> str:
    """Returns the v2 path from /proc/self/cgroup (the '0::' line)."""
    if not text:
        return ""
    for line in text.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[0] == "0":
            return parts[2].strip()
    return ""


def parse_cgroup_stat(text: Optional[str]) -> dict:
    if not text:
        return {}
    out: dict = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].lstrip("-").isdigit():
            out[parts[0]] = int(parts[1])
    return out


def classify(controllers: Set[str], subtree: Set[str],
              hybrid_dirs: List[str], own_path: str,
              stat: dict) -> dict:
    if not controllers:
        return {"verdict": "unknown",
                "reason": "/sys/fs/cgroup not readable.",
                "recommendation": ""}

    # 1) hybrid_v1_v2
    if hybrid_dirs:
        return {"verdict": "hybrid_v1_v2",
                "reason": (f"Found v1 controller dirs under cgroup "
                          f"root : {', '.join(hybrid_dirs)}. "
                          f"Accounting splits across two "
                          f"hierarchies."),
                "recommendation": _recipe_unify_v2()}

    # 2) missing_controllers
    missing = controllers - subtree
    if missing:
        return {"verdict": "missing_controllers",
                "reason": (f"cgroup.controllers has "
                          f"{len(controllers)} controllers, but "
                          f"cgroup.subtree_control delegates only "
                          f"{len(subtree)} → missing : "
                          f"{', '.join(sorted(missing))}. "
                          f"Systemd slices silently inherit no "
                          f"quota."),
                "recommendation": _recipe_delegate(missing)}

    # 3) deep_nesting
    if own_path:
        depth = own_path.count("/")
        if depth > 5:
            return {"verdict": "deep_nesting",
                    "reason": (f"Daemon cgroup path is "
                              f"'{own_path}' — depth {depth} > 5. "
                              f"Descends through cgroup.events / "
                              f"freeze APIs slowly."),
                    "recommendation": _recipe_flatten()}

    return {"verdict": "ok",
            "reason": (f"v2 unified, {len(controllers)} controllers "
                      f"fully delegated, descendants = "
                      f"{stat.get('nr_descendants', '?')}."),
            "recommendation": ""}


def status(config=None,
            sys_cgroup: str = _SYS_CGROUP,
            proc_self_cgroup: str = _PROC_SELF_CGROUP) -> dict:
    controllers = _read_tokens(os.path.join(sys_cgroup,
                                                "cgroup.controllers"))
    subtree = _read_tokens(os.path.join(sys_cgroup,
                                             "cgroup.subtree_control"))
    hybrid_dirs = detect_hybrid(sys_cgroup)
    own_path = parse_self_cgroup(_read(proc_self_cgroup))
    stat = parse_cgroup_stat(_read(os.path.join(sys_cgroup,
                                                      "cgroup.stat")))
    max_depth = _read(os.path.join(sys_cgroup,
                                        "cgroup.max.depth")) or ""
    max_desc = _read(os.path.join(sys_cgroup,
                                       "cgroup.max.descendants")) or ""
    ok = bool(controllers)
    verdict = classify(controllers, subtree, hybrid_dirs,
                          own_path, stat)
    return {"ok": ok,
              "controllers": sorted(controllers),
              "subtree_control": sorted(subtree),
              "hybrid_v1_dirs": hybrid_dirs,
              "own_cgroup_path": own_path,
              "stat": stat,
              "max_depth": max_depth.strip(),
              "max_descendants": max_desc.strip(),
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_unify_v2() -> str:
    return ("# Move to unified cgroup v2 by adding the kernel\n"
            "# cmdline arg :\n"
            "echo '  systemd.unified_cgroup_hierarchy=1' \\\n"
            "  | sudo tee -a /etc/default/grub  # then update-grub\n"
            "# … and on Debian/Ubuntu, install :\n"
            "sudo apt install systemd-resolved && sudo reboot\n")


def _recipe_delegate(missing: Set[str]) -> str:
    pluses = " ".join("+" + c for c in sorted(missing))
    return (f"# Delegate the missing controllers to subtrees :\n"
            f"echo '{pluses}' | sudo tee /sys/fs/cgroup/cgroup.subtree_control\n"
            f"# Persist via /etc/systemd/system.conf.d/ :\n"
            f"#   [Manager]\n"
            f"#   DefaultCPUAccounting=yes\n"
            f"#   DefaultMemoryAccounting=yes\n"
            f"#   DefaultIOAccounting=yes\n")


def _recipe_flatten() -> str:
    return ("# Reduce cgroup nesting by moving the daemon to a\n"
            "# top-level slice :\n"
            "sudo systemctl edit gpu-dashboard.service\n"
            "#   [Service]\n"
            "#   Slice=app.slice\n"
            "# Then reload + restart the unit.\n")
