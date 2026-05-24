"""Module uevent_helper_audit — kernel uevent helper / hotplug
script audit (R&D #72.4).

Two related kernel knobs let userspace register an executable
that the kernel runs synchronously on every uevent
(hot-plug, module load, etc.) :

  /sys/kernel/uevent_helper   modern interface (write a path)
  /proc/sys/kernel/hotplug    legacy interface (same purpose)

When non-empty, the kernel fork-execs the named binary as root
on every uevent. This is :

  * Hugely expensive (CONFIG_UEVENT_HELPER warning :
    "should not be enabled on production systems").
  * A standard privilege-escalation surface if the path is
    writable by a less-trusted user.

systemd-udevd uses netlink notification instead and leaves both
files empty. Anything non-empty here is almost always a
debugging leftover OR a deliberate backdoor.

Verdicts (priority order) :
  uevent_helper_set_to_script   /sys/kernel/uevent_helper has a
                                  non-empty path.
  hotplug_handler_set           /proc/sys/kernel/hotplug has a
                                  non-empty path (legacy
                                  interface).
  requires_root                  files exist but unreadable.
  ok                             both empty (systemd-udevd /
                                  modern hotplug stack).
  unknown                        neither knob present (kernel
                                  built without
                                  CONFIG_UEVENT_HELPER and the
                                  /proc/sys/kernel/hotplug sysctl
                                  also absent).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "uevent_helper_audit"


_SYS_UEVENT_HELPER = "/sys/kernel/uevent_helper"
_PROC_HOTPLUG = "/proc/sys/kernel/hotplug"


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def read_uevent_helper(path: str = _SYS_UEVENT_HELPER) -> dict:
    out = {"present": False, "readable": False,
              "value": None}
    if not os.path.exists(path):
        return out
    out["present"] = True
    txt = _read(path)
    if txt is None:
        out["readable"] = False
        return out
    out["readable"] = True
    out["value"] = txt.strip()
    return out


def read_hotplug(path: str = _PROC_HOTPLUG) -> dict:
    return read_uevent_helper(path)


def classify(ue: dict, hp: dict) -> dict:
    if not (ue["present"] or hp["present"]):
        return {"verdict": "unknown",
                "reason": ("Neither /sys/kernel/uevent_helper "
                          "nor /proc/sys/kernel/hotplug is "
                          "present — uevent helper feature "
                          "absent from this kernel."),
                "recommendation": ""}

    # 1) uevent_helper_set_to_script
    if ue["readable"] and ue["value"]:
        return {"verdict": "uevent_helper_set_to_script",
                "reason": (f"/sys/kernel/uevent_helper is set to "
                          f"'{ue['value']}'. Kernel will fork-"
                          f"exec this path as root on every "
                          f"uevent."),
                "recommendation": _recipe_uevent_set(
                    ue["value"])}

    # 2) hotplug_handler_set
    if hp["readable"] and hp["value"]:
        return {"verdict": "hotplug_handler_set",
                "reason": (f"/proc/sys/kernel/hotplug is set to "
                          f"'{hp['value']}'. Legacy hotplug "
                          f"interface active."),
                "recommendation": _recipe_hotplug_set(
                    hp["value"])}

    # 3) requires_root — at least one file present but
    #    unreadable.
    if ((ue["present"] and not ue["readable"])
            or (hp["present"] and not hp["readable"])):
        return {"verdict": "requires_root",
                "reason": ("uevent_helper / hotplug knob present "
                          "but unreadable as this user."),
                "recommendation": _recipe_requires_root()}

    return {"verdict": "ok",
            "reason": ("uevent_helper and hotplug are both "
                      "empty — modern netlink-based udev "
                      "stack in use."),
            "recommendation": ""}


def status(config=None,
            sys_uevent_helper: str = _SYS_UEVENT_HELPER,
            proc_hotplug: str = _PROC_HOTPLUG) -> dict:
    ue = read_uevent_helper(sys_uevent_helper)
    hp = read_hotplug(proc_hotplug)
    verdict = classify(ue, hp)
    return {"ok": ue["present"] or hp["present"],
              "uevent_helper_present": ue["present"],
              "uevent_helper_readable": ue["readable"],
              "uevent_helper_value": ue["value"],
              "hotplug_present": hp["present"],
              "hotplug_readable": hp["readable"],
              "hotplug_value": hp["value"],
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_uevent_set(path: str) -> str:
    return (f"# /sys/kernel/uevent_helper is set to '{path}'.\n"
            f"# This path is fork-exec'd as root on every uevent.\n"
            f"# Clear it (back to systemd-udevd netlink path) :\n"
            f"echo '' | sudo tee /sys/kernel/uevent_helper\n"
            f"# Audit the script :\n"
            f"ls -l '{path}'\n"
            f"sudo cat '{path}'\n")


def _recipe_hotplug_set(path: str) -> str:
    return (f"# Legacy /proc/sys/kernel/hotplug is set to "
            f"'{path}'.\n"
            f"# Clear via sysctl :\n"
            f"sudo sysctl -w kernel.hotplug=\n"
            f"# Audit the handler :\n"
            f"ls -l '{path}' && sudo cat '{path}'\n")


def _recipe_requires_root() -> str:
    return ("# uevent_helper / hotplug values are root-readable\n"
            "# only on some hardened kernels. Inspect as root :\n"
            "sudo cat /sys/kernel/uevent_helper\n"
            "sudo cat /proc/sys/kernel/hotplug\n")
