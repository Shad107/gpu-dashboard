"""Module smt_audit — SMT toggle + offline-core audit (R&D #35.4).

For LLM inference rigs the SMT (simultaneous multithreading, aka
hyperthreading) toggle and the per-core online state are two
levers users hit with mixed results:

  - SMT *on*  → 2× logical cores, but each thread gets ~50-60 % of a
                physical core under load. llama.cpp prompt-processing
                benefits modestly because of memory parallelism, but
                tight CUDA-host loops can suffer cache pressure.
  - SMT *off* → fewer logical cores, each gets 100 % of a physical
                core. Useful for low-latency token generation.
  - cores offlined for thermal / power-cap reasons → wasted hardware
                that nobody remembers to bring back.

sysfs surfaces:
  /sys/devices/system/cpu/smt/control       on/off/forceoff/notsupported
  /sys/devices/system/cpu/smt/active        0=off, 1=on
  /sys/devices/system/cpu/possible          "0-N" range of allocated CPUs
  /sys/devices/system/cpu/online            "0,2-5" current online mask
  /sys/devices/system/cpu/offline           "1,6-7" current offline mask
  /sys/devices/system/cpu/cpu<n>/online     per-CPU (0=offline, 1=online).
                                            cpu0 has no `online` file —
                                            boot CPU can't be offlined.

Verdicts (priority order, worst-pick):
  cores_offline      one or more cores explicitly offlined while
                     SMT is `on` — capacity wasted ; recipe is a
                     paste-ready `echo 1 > .../cpu<n>/online` block
  smt_off           SMT explicitly disabled — informational, user
                    choice ; recipe to re-enable (echo on > control)
  smt_on             SMT enabled, all online — default healthy state
  smt_not_supported  control=notsupported — VM or non-SMT CPU
  unknown            cannot read sysfs

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "smt_audit"


_CPU_ROOT = "/sys/devices/system/cpu"


_RANGE_RE = re.compile(r"^(\d+)(?:-(\d+))?$")


def parse_cpu_list(s: Optional[str]) -> list:
    if not s:
        return []
    out: list = []
    for tok in s.strip().split(","):
        m = _RANGE_RE.match(tok.strip())
        if not m:
            continue
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        out.extend(range(a, b + 1))
    return sorted(set(out))


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def read_smt_control(root: str = _CPU_ROOT) -> Optional[str]:
    return _read(os.path.join(root, "smt", "control"))


def read_smt_active(root: str = _CPU_ROOT) -> Optional[int]:
    s = _read(os.path.join(root, "smt", "active"))
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def find_offline_cores(root: str = _CPU_ROOT) -> list:
    """Prefer the canonical `offline` mask; fall back to scanning per-cpu
    online files for kernels that don't expose the aggregated mask."""
    s = _read(os.path.join(root, "offline"))
    if s:
        cpus = parse_cpu_list(s)
        if cpus:
            return cpus
    # Fallback: scan per-cpu online files
    out: list = []
    try:
        names = os.listdir(root)
    except OSError:
        return []
    for n in names:
        m = re.match(r"^cpu(\d+)$", n)
        if not m:
            continue
        on = _read(os.path.join(root, n, "online"))
        if on is not None and on.strip() == "0":
            out.append(int(m.group(1)))
    return sorted(out)


_RECIPE_REENABLE_CORES = lambda cores: (
    "# Re-enable offlined cores (root, runtime — no persistence):\n"
    + "\n".join(
        f"echo 1 | sudo tee /sys/devices/system/cpu/cpu{c}/online"
        for c in cores
    )
    + "\n# Persist via systemd-user override or a kernel cmdline that\n"
    + "# omits `maxcpus=` / `nr_cpus=`."
)


_RECIPE_REENABLE_SMT = (
    "# Re-enable SMT (root, runtime):\n"
    "echo on | sudo tee /sys/devices/system/cpu/smt/control\n"
    "# If SMT was disabled via cmdline, check /proc/cmdline for\n"
    "# `nosmt` / `mitigations=auto,nosmt` and remove from\n"
    "# /etc/default/grub if you want it permanent."
)


def classify(smt_control: Optional[str], smt_active: Optional[int],
              possible_count: int, online_count: int,
              offline_cores: list) -> dict:
    # `smt_off` outranks `cores_offline` semantically — when SMT is
    # explicitly disabled, the offlined hyperthread siblings are *the*
    # configuration, not wasted hardware.
    if smt_control is None and smt_active is None:
        return {"verdict": "unknown",
                "reason": "/sys/devices/system/cpu/smt/* absent.",
                "recommendation": ""}
    ctrl = (smt_control or "").lower()
    if ctrl in ("off", "forceoff") or (
            ctrl == "notsupported" and smt_active == 1
        ) is False and smt_active == 0 and ctrl in ("off", "forceoff"):
        return {"verdict": "smt_off",
                "reason": (f"SMT explicitly disabled "
                           f"(smt_control={smt_control}). Hyperthread "
                           f"siblings are offline by design."),
                "recommendation": _RECIPE_REENABLE_SMT}
    # Explicit off
    if ctrl in ("off", "forceoff"):
        return {"verdict": "smt_off",
                "reason": (f"SMT explicitly disabled "
                           f"(smt_control={smt_control}). Hyperthread "
                           f"siblings are offline by design."),
                "recommendation": _RECIPE_REENABLE_SMT}
    # Offline cores while SMT is on/notsupported
    if offline_cores:
        return {"verdict": "cores_offline",
                "reason": (f"Cores {offline_cores} are offline while "
                           f"SMT is on/notsupported. {online_count} of "
                           f"{possible_count} possible CPUs are active — "
                           f"capacity wasted."),
                "recommendation": _RECIPE_REENABLE_CORES(offline_cores)}
    if ctrl == "notsupported":
        return {"verdict": "smt_not_supported",
                "reason": ("smt_control=notsupported — host doesn't "
                           "expose an SMT toggle (VM, AMD non-SMT part, "
                           "or kernel without CONFIG_HOTPLUG_CPU)."),
                "recommendation": ""}
    if ctrl == "on" or smt_active == 1:
        return {"verdict": "smt_on",
                "reason": (f"SMT enabled, all {online_count} CPUs "
                           f"online — default healthy state."),
                "recommendation": ""}
    return {"verdict": "unknown",
            "reason": f"smt_control={smt_control}, smt_active={smt_active}.",
            "recommendation": ""}


def status(cfg=None) -> dict:
    smt_control = read_smt_control(_CPU_ROOT)
    smt_active = read_smt_active(_CPU_ROOT)
    possible = parse_cpu_list(_read(os.path.join(_CPU_ROOT, "possible")))
    online = parse_cpu_list(_read(os.path.join(_CPU_ROOT, "online")))
    offline_cores = find_offline_cores(_CPU_ROOT)
    verdict = classify(smt_control, smt_active, len(possible),
                         len(online), offline_cores)
    return {
        "ok": True,
        "smt_control": smt_control,
        "smt_active": smt_active,
        "possible_count": len(possible),
        "online_count": len(online),
        "offline_cores": offline_cores,
        "verdict": verdict,
    }
