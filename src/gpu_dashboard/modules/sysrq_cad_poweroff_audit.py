"""Module sysrq_cad_poweroff_audit — Ctrl-Alt-Del + poweroff_cmd
posture (R&D #107.2).

sysrq_mask_audit covers /proc/sys/kernel/sysrq only. panic_policy
covers kernel.panic / panic_on_oops / panic_on_warn / nmi_watchdog.
No existing module touches Ctrl-Alt-Del semantics or the kernel's
poweroff command path.

Reads :

  /proc/sys/kernel/ctrl-alt-del
    0 = soft (kernel notifies cad_pid / PID 1)
    1 = hard reboot — skips init, BAD for desktops
  /proc/sys/kernel/poweroff_cmd
    Path the kernel exec()s for software-controlled poweroff.
    Default '/sbin/poweroff'. Override = supply-chain risk.

Verdicts (worst-first) :

  cad_hard_reboot         warn    ctrl-alt-del=1 — Ctrl-Alt-Del
                                  triggers immediate hard reboot,
                                  skips userspace shutdown.
  poweroff_cmd_overridden accent  poweroff_cmd != '/sbin/poweroff'
                                  — non-default binary runs at
                                  shutdown, audit it.
  ok                              defaults intact.
  requires_root                   sysctl unreadable.
  unknown                         /proc/sys/kernel absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "sysrq_cad_poweroff_audit"

DEFAULT_SYSCTL = "/proc/sys/kernel"
_DEFAULT_POWEROFF_CMD = "/sbin/poweroff"


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


def _read_str(path: str) -> Optional[str]:
    t = _read_text(path)
    return t.strip() if t is not None else None


def classify(present: bool,
             cad: Optional[int],
             poweroff_cmd: Optional[str]) -> dict:
    if not present:
        return {"verdict": "unknown",
                "reason": "/proc/sys/kernel absent."}
    if cad is None and poweroff_cmd is None:
        return {"verdict": "requires_root",
                "reason": (
                    "CAD/poweroff_cmd unreadable — "
                    "re-run as root.")}

    # warn — hard reboot on CAD
    if cad == 1:
        return {
            "verdict": "cad_hard_reboot",
            "reason": (
                "ctrl-alt-del=1 — Ctrl-Alt-Del triggers "
                "an immediate hard reboot, skipping "
                "userspace shutdown. Lose in-flight "
                "writes, surprise users.")}

    # accent — poweroff_cmd overridden
    if (poweroff_cmd is not None
            and poweroff_cmd != _DEFAULT_POWEROFF_CMD
            and poweroff_cmd != ""):
        return {
            "verdict": "poweroff_cmd_overridden",
            "reason": (
                f"poweroff_cmd={poweroff_cmd!r} (default "
                f"{_DEFAULT_POWEROFF_CMD!r}). Kernel "
                "exec()s this binary at shutdown — audit "
                "the path / signature.")}

    return {"verdict": "ok",
            "reason": (
                f"ctrl-alt-del={cad} ; "
                f"poweroff_cmd={poweroff_cmd!r}. Sane.")}


def status(config: Optional[dict] = None,
           sysctl: str = DEFAULT_SYSCTL) -> dict:
    present = os.path.isdir(sysctl)
    cad = (
        _read_int(os.path.join(sysctl, "ctrl-alt-del"))
        if present else None)
    poweroff_cmd = (
        _read_str(os.path.join(sysctl, "poweroff_cmd"))
        if present else None)
    verdict = classify(present, cad, poweroff_cmd)
    return {
        "ok": verdict["verdict"] == "ok",
        "ctrl_alt_del": cad,
        "poweroff_cmd": poweroff_cmd,
        "verdict": verdict,
    }
