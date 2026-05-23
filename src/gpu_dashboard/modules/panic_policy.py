"""Module panic_policy — headless-rig panic auto-reboot auditor (R&D #41.3).

A headless LLM rig sitting in a closet should respond to a kernel
panic / hung-task / softlockup by *rebooting itself*, not by hanging
the BMC console forever and forcing the owner to drive there with
a keyboard. The kernel exposes the reboot-on-panic policy via :

  /proc/sys/kernel/panic                   seconds to wait before
                                           rebooting after panic
                                           (0 = wait forever — bad
                                           for headless).
  /proc/sys/kernel/panic_on_oops           1 = treat oops as panic
                                           (otherwise the box keeps
                                           running in a corrupted
                                           state — also bad).
  /proc/sys/kernel/hung_task_panic         1 = panic when a task
                                           is stuck > N seconds.
  /proc/sys/kernel/hung_task_timeout_secs  N (default 120) — how
                                           long a task must hang
                                           before kernel notices.
  /proc/sys/kernel/softlockup_panic        1 = panic on softlockup
                                           detection (a CPU spinning
                                           > 20s without scheduling).
  /proc/sys/kernel/panic_on_io_nmi         1 = panic on IO NMI
                                           (typically hardware fail).
  /proc/sys/kernel/panic_on_unrecovered_nmi 1 = panic on unrecovered
                                           NMI (PCIe AER catastrophe).
  /proc/sys/kernel/panic_on_warn           1 = panic on any WARN()
                                           (too aggressive ; off OK).
  /proc/sys/kernel/nmi_watchdog            1 = watchdog enabled (the
                                           thing that *detects*
                                           softlockup in the first
                                           place).

Verdicts :
  ok_auto_reboot              panic > 0 + panic_on_oops=1 ; the box
                              will reboot after a fault. Hung-task
                              + softlockup panic optional but
                              recommended on a headless rig.
  stuck_forever_on_panic      panic=0 — kernel hangs the console
                              forever. Worst case for a headless
                              rig — recipe to set panic=10.
  silent_on_hung_task         hung_task_panic=0 + the rig is
                              headless (host_class server / vm /
                              kvm_host) → a wedged inference worker
                              just spins forever invisible. Recommend
                              hung_task_panic=1.
  watchdog_disabled           nmi_watchdog=0 → softlockup detection
                              is itself off, panic on softlockup
                              can't fire.
  unknown                     /proc/sys/kernel unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "panic_policy"


_PROC_SYS_KERNEL = "/proc/sys/kernel"


_FIELDS_INT = (
    "panic", "panic_on_oops", "panic_on_io_nmi",
    "panic_on_unrecovered_nmi", "panic_on_warn",
    "hung_task_panic", "hung_task_timeout_secs",
    "hung_task_warnings", "softlockup_panic",
    "softlockup_all_cpu_backtrace", "nmi_watchdog",
    "oops_limit", "print_fatal_signals",
)


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


def read_knobs(sysk: str = _PROC_SYS_KERNEL) -> dict:
    out: dict = {}
    for f in _FIELDS_INT:
        v = _read_int(os.path.join(sysk, f))
        if v is not None:
            out[f] = v
    return out


_HEADLESS_FORMS = ("server", "vm", "kvm_host", "embedded", "mini_pc")


def _is_headless(host_form_factor: Optional[str]) -> bool:
    return host_form_factor in _HEADLESS_FORMS


_RECIPE_PANIC_REBOOT = (
    "# Headless rig should reboot 10 s after panic instead of\n"
    "# hanging the console forever :\n"
    "echo 10 | sudo tee /proc/sys/kernel/panic\n"
    "# Persistent :\n"
    "sudo tee /etc/sysctl.d/99-panic-reboot.conf <<'EOF'\n"
    "kernel.panic = 10\n"
    "kernel.panic_on_oops = 1\n"
    "kernel.panic_on_io_nmi = 1\n"
    "kernel.panic_on_unrecovered_nmi = 1\n"
    "EOF\n"
    "sudo sysctl --system"
)

_RECIPE_HUNG_TASK = (
    "# Make a wedged inference worker panic-reboot the headless box\n"
    "# instead of spinning forever invisible :\n"
    "sudo tee /etc/sysctl.d/99-hung-task.conf <<'EOF'\n"
    "kernel.hung_task_panic = 1\n"
    "kernel.hung_task_timeout_secs = 120\n"
    "kernel.softlockup_panic = 1\n"
    "EOF\n"
    "sudo sysctl --system"
)

_RECIPE_WATCHDOG = (
    "# nmi_watchdog is off — softlockup detection can't fire.\n"
    "# Enable it (cost : tiny CPU overhead, ~1 NMI per second\n"
    "# per CPU) :\n"
    "echo 1 | sudo tee /proc/sys/kernel/nmi_watchdog\n"
    "echo 'kernel.nmi_watchdog = 1' | \\\n"
    "  sudo tee /etc/sysctl.d/99-nmi-watchdog.conf"
)


_RANK = {"ok_auto_reboot": 0, "watchdog_disabled": 1,
         "silent_on_hung_task": 2, "stuck_forever_on_panic": 3,
         "unknown": 0}


def classify(knobs: dict,
              host_form_factor: Optional[str] = None) -> dict:
    if not knobs:
        return {"verdict": "unknown",
                "reason": "/proc/sys/kernel unreadable.",
                "recommendation": ""}
    panic_sec = knobs.get("panic")
    panic_on_oops = knobs.get("panic_on_oops")
    hung_task_panic = knobs.get("hung_task_panic", 0)
    nmi_watchdog = knobs.get("nmi_watchdog")
    softlockup_panic = knobs.get("softlockup_panic", 0)
    # Stuck-forever is the worst case for a headless rig — flag first.
    if panic_sec == 0:
        return {"verdict": "stuck_forever_on_panic",
                "reason": ("kernel.panic=0 — on panic, the box "
                           "waits forever for a human at the "
                           "console to press the reset button. "
                           "Bad for a headless rig."),
                "recommendation": _RECIPE_PANIC_REBOOT}
    # panic_on_oops=0 → oops doesn't escalate, kernel keeps running
    # in a corrupted state. Same risk class as stuck_forever for an
    # autonomous rig.
    if panic_on_oops == 0:
        return {"verdict": "stuck_forever_on_panic",
                "reason": ("kernel.panic_on_oops=0 — kernel keeps "
                           "running after an oops, leaving the "
                           "system in a corrupted but live state. "
                           "Combine with panic=10 for clean "
                           "auto-recovery."),
                "recommendation": _RECIPE_PANIC_REBOOT}
    # Watchdog off means softlockup can never fire.
    if nmi_watchdog == 0:
        return {"verdict": "watchdog_disabled",
                "reason": ("kernel.nmi_watchdog=0 — softlockup "
                           "detection itself is disabled. A spinning "
                           "CPU will never trigger softlockup_panic."),
                "recommendation": _RECIPE_WATCHDOG}
    # On a headless rig, hung_task_panic=0 is a real problem.
    if _is_headless(host_form_factor) and (
            hung_task_panic == 0 or softlockup_panic == 0):
        which = []
        if hung_task_panic == 0:
            which.append("hung_task_panic=0")
        if softlockup_panic == 0:
            which.append("softlockup_panic=0")
        return {"verdict": "silent_on_hung_task",
                "reason": (f"Headless host ({host_form_factor}) with "
                           f"{', '.join(which)} — a stuck inference "
                           f"worker or wedged CPU will not auto-"
                           f"reboot, leaving the rig invisible "
                           f"until the owner notices."),
                "recommendation": _RECIPE_HUNG_TASK}
    return {"verdict": "ok_auto_reboot",
            "reason": (f"kernel.panic={panic_sec} + "
                       f"panic_on_oops={panic_on_oops}"
                       + (f" + hung_task_panic=1" if hung_task_panic
                            else "")
                       + (f" + softlockup_panic=1" if softlockup_panic
                            else "")
                       + " — auto-recovery on fault."),
            "recommendation": ""}


def _try_host_form_factor(cfg) -> Optional[str]:
    try:
        from . import host_class
        out = host_class.status(cfg)
        if out.get("ok"):
            return (out.get("verdict") or {}).get("verdict")
    except Exception:
        pass
    return None


def status(cfg=None) -> dict:
    knobs = read_knobs(_PROC_SYS_KERNEL)
    host_form_factor = _try_host_form_factor(cfg)
    verdict = classify(knobs, host_form_factor)
    return {
        "ok": bool(knobs),
        "knobs": knobs,
        "host_form_factor": host_form_factor,
        "verdict": verdict,
    }
