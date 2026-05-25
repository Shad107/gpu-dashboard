"""Module oom_policy_sysctl_audit — kernel OOM decision-policy
sysctls (R&D #99.3).

The OOM killer has three policy knobs that turn a single
runaway process into either a clean reclaim or a desktop
reboot:

  vm.panic_on_oom
    0 = kill the heaviest process (default, correct)
    1 = panic the kernel (reboot the box)
    2 = panic only if cpuset/memcg OOMs
  vm.oom_kill_allocating_task
    0 = kill the heaviest task (default, correct)
    1 = kill whoever happened to ask for memory
  vm.oom_dump_tasks
    0 = no per-task dump on OOM (post-mortems blind)
    1 = dump all tasks (default, correct)

Existing modules audit *priority* (oom_priority, oom_score_adj
on running tasks) and *correlation* (oomd_correlator parsing
systemd-oomd events vs PSI), and panic_policy covers
`kernel.panic` + `panic_on_warn` + `hardlockup_panic`. None
of them check vm.panic_on_oom / oom_kill_allocating_task /
oom_dump_tasks.

Reads :

  /proc/sys/vm/panic_on_oom
  /proc/sys/vm/oom_kill_allocating_task
  /proc/sys/vm/oom_dump_tasks
  /proc/sys/vm/overcommit_kbytes
  /proc/meminfo                  (MemTotal sanity)

Verdicts (worst-first) :

  panic_on_oom_set       err     vm.panic_on_oom != 0 — any
                                 OOM = reboot. Catastrophic on
                                 a single-user desktop.
  kill_allocating_task   warn    vm.oom_kill_allocating_task=1
                                 — kills the caller, not the
                                 fattest tenant. Wrong heur
                                 for an LLM rig.
  dump_tasks_disabled    accent  vm.oom_dump_tasks=0 — kernel
                                 won't list tasks on OOM ;
                                 post-mortem blind.
  ok                             policy knobs all sane.
  requires_root                  /proc/sys/vm unreadable.
  unknown                        /proc/sys/vm absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "oom_policy_sysctl_audit"

DEFAULT_VM_SYSCTL = "/proc/sys/vm"
DEFAULT_MEMINFO = "/proc/meminfo"


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


def parse_meminfo_total(text: Optional[str]) -> Optional[int]:
    """Return MemTotal in kB, or None."""
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1])
                except ValueError:
                    return None
    return None


def classify(vm_present: bool,
             panic_on_oom: Optional[int],
             kill_allocating: Optional[int],
             dump_tasks: Optional[int]) -> dict:
    if not vm_present:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/sys/vm absent — kernel without "
                    "/proc/sys (highly unusual).")}
    if (panic_on_oom is None
            and kill_allocating is None
            and dump_tasks is None):
        return {"verdict": "requires_root",
                "reason": (
                    "OOM policy sysctls unreadable — re-run "
                    "as root.")}

    # err — any non-zero panic_on_oom on a desktop is wrong
    if panic_on_oom and panic_on_oom != 0:
        return {
            "verdict": "panic_on_oom_set",
            "reason": (
                f"vm.panic_on_oom={panic_on_oom} — every "
                "OOM event reboots the desktop. Only ever "
                "correct on HA cluster nodes.")}

    # warn — oom_kill_allocating_task=1 is the wrong heuristic
    if kill_allocating == 1:
        return {
            "verdict": "kill_allocating_task",
            "reason": (
                "vm.oom_kill_allocating_task=1 — OOM kills "
                "whoever asked for memory, not the fattest "
                "tenant. A bash shell can die instead of "
                "the llama.cpp that ate the RAM.")}

    # accent — dump_tasks=0 blinds post-mortems
    if dump_tasks == 0:
        return {
            "verdict": "dump_tasks_disabled",
            "reason": (
                "vm.oom_dump_tasks=0 — kernel won't list "
                "per-task RSS on OOM. Post-mortem blind ; "
                "won't see which process caused it.")}

    return {"verdict": "ok",
            "reason": (
                f"panic_on_oom={panic_on_oom} ; "
                f"kill_allocating={kill_allocating} ; "
                f"dump_tasks={dump_tasks}. OOM policy sane.")}


def status(config: Optional[dict] = None,
           vm_sysctl: str = DEFAULT_VM_SYSCTL,
           meminfo: str = DEFAULT_MEMINFO) -> dict:
    vm_present = os.path.isdir(vm_sysctl)
    panic_on_oom = (
        _read_int(os.path.join(vm_sysctl, "panic_on_oom"))
        if vm_present else None)
    kill_allocating = (
        _read_int(os.path.join(
            vm_sysctl, "oom_kill_allocating_task"))
        if vm_present else None)
    dump_tasks = (
        _read_int(os.path.join(vm_sysctl, "oom_dump_tasks"))
        if vm_present else None)
    mem_total = parse_meminfo_total(_read_text(meminfo))

    verdict = classify(vm_present, panic_on_oom,
                       kill_allocating, dump_tasks)
    return {
        "ok": verdict["verdict"] == "ok",
        "panic_on_oom": panic_on_oom,
        "oom_kill_allocating_task": kill_allocating,
        "oom_dump_tasks": dump_tasks,
        "mem_total_kb": mem_total,
        "verdict": verdict,
    }
