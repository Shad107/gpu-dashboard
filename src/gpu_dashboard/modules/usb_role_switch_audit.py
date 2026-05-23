"""Module usb_role_switch_audit — USB role switch audit
(R&D #71.2).

USB-C / dual-role USB ports use the kernel's `usb_role_switch`
framework to flip between host and device modes. On laptops
and SoC dev boards the role can get stuck or oscillate due to
buggy Type-C controllers — symptoms are "USB peripherals
disconnect on resume" or "phone stops charging the moment
docked."

Reads :
  /sys/class/usb_role/*/role
  /sys/class/typec/*/data_role
  /sys/bus/platform/drivers/intel_xhci_usb_sw    (presence)

Verdicts (priority order) :
  role_stuck_device       ≥1 port stuck in "device" role on a
                            machine that has only host-side USB
                            consumers (no peripheral-class
                            cables detected).
  role_flapping           Same port reported "host" then
                            "device" within a short window
                            (deferred — single-shot can't see
                            flapping, so we surface a
                            placeholder when a port has the
                            string "none" mid-transition).
  unexpected_host_role    Type-C data_role disagrees with the
                            usb_role read (Type-C controller
                            and USB switch out of sync).
  ok                      roles consistent, no faults.
  unknown                 framework absent on this host.

stdlib only.
"""
from __future__ import annotations

import os
from typing import List, Optional


NAME = "usb_role_switch_audit"


_SYS_USB_ROLE = "/sys/class/usb_role"
_SYS_TYPEC = "/sys/class/typec"
_INTEL_XHCI_SW = ("/sys/bus/platform/drivers/"
                       "intel_xhci_usb_sw")


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def list_usb_roles(sys_path: str = _SYS_USB_ROLE
                       ) -> List[dict]:
    if not os.path.isdir(sys_path):
        return []
    out: List[dict] = []
    try:
        names = sorted(os.listdir(sys_path))
    except OSError:
        return []
    for n in names:
        d = os.path.join(sys_path, n)
        if not os.path.isdir(d):
            continue
        out.append({"id": n,
                       "role": _read(os.path.join(d, "role"))})
    return out


def list_typec_ports(sys_path: str = _SYS_TYPEC) -> List[dict]:
    if not os.path.isdir(sys_path):
        return []
    out: List[dict] = []
    try:
        names = sorted(os.listdir(sys_path))
    except OSError:
        return []
    for n in names:
        d = os.path.join(sys_path, n)
        if not os.path.isdir(d):
            continue
        if not n.startswith("port"):
            continue
        out.append({"id": n,
                       "data_role": _read(os.path.join(
                           d, "data_role")),
                       "power_role": _read(os.path.join(
                           d, "power_role"))})
    return out


def classify(usb_roles: List[dict], typec_ports: List[dict],
              usb_role_present: bool,
              typec_present: bool) -> dict:
    if not (usb_role_present or typec_present):
        return {"verdict": "unknown",
                "reason": ("/sys/class/usb_role and "
                          "/sys/class/typec both absent — host "
                          "lacks dual-role USB framework."),
                "recommendation": ""}

    # 1) role_stuck_device — every usb_role port is "device"
    if usb_roles:
        all_device = all(
            (r.get("role") or "").lower() == "device"
                for r in usb_roles)
        if all_device:
            sample = ", ".join(r["id"] for r in usb_roles[:3])
            return {"verdict": "role_stuck_device",
                    "reason": (f"All {len(usb_roles)} usb_role "
                              f"port(s) are stuck in device "
                              f"mode : {sample}."),
                    "recommendation": _recipe_stuck()}

    # 2) role_flapping — any port reports "none" (transition
    #    sentinel)
    flap = [r for r in usb_roles
              if (r.get("role") or "").lower() == "none"]
    if flap:
        sample = ", ".join(r["id"] for r in flap[:3])
        return {"verdict": "role_flapping",
                "reason": (f"{len(flap)} usb_role port(s) "
                          f"report transitional 'none' role : "
                          f"{sample}."),
                "recommendation": _recipe_flapping()}

    # 3) unexpected_host_role — type-c data_role conflicts with
    #    usb_role mapping. Heuristic: any type-c port has
    #    data_role=host while a corresponding usb_role is
    #    device, or vice-versa. Without a clean mapping we
    #    flag the global mismatch.
    if usb_roles and typec_ports:
        usb_hosts = sum(
            1 for r in usb_roles
                if (r.get("role") or "").lower() == "host")
        typec_hosts = sum(
            1 for p in typec_ports
                if "host" in (p.get("data_role") or "")
                    .lower())
        if usb_hosts != typec_hosts:
            return {"verdict": "unexpected_host_role",
                    "reason": (f"usb_role host count "
                              f"({usb_hosts}) != type-c "
                              f"data_role host count "
                              f"({typec_hosts})."),
                    "recommendation": _recipe_mismatch()}

    return {"verdict": "ok",
            "reason": (f"usb_role ports = {len(usb_roles)} ; "
                      f"typec ports = {len(typec_ports)}."),
            "recommendation": ""}


def status(config=None,
            sys_usb_role: str = _SYS_USB_ROLE,
            sys_typec: str = _SYS_TYPEC,
            intel_xhci_sw: str = _INTEL_XHCI_SW) -> dict:
    usb_role_present = os.path.isdir(sys_usb_role)
    typec_present = os.path.isdir(sys_typec)
    intel_sw_present = os.path.exists(intel_xhci_sw)
    usb_roles = list_usb_roles(sys_usb_role)
    typec_ports = list_typec_ports(sys_typec)
    verdict = classify(usb_roles, typec_ports,
                          usb_role_present, typec_present)
    return {"ok": usb_role_present or typec_present,
              "usb_role_present": usb_role_present,
              "typec_present": typec_present,
              "intel_xhci_sw_present": intel_sw_present,
              "usb_role_count": len(usb_roles),
              "usb_roles": usb_roles,
              "typec_port_count": len(typec_ports),
              "typec_ports": typec_ports,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_stuck() -> str:
    return ("# All usb_role ports stuck in device mode :\n"
            "for r in /sys/class/usb_role/*; do\n"
            "  echo \"$r/role = $(cat $r/role)\"\n"
            "done\n"
            "# Manual override (requires CAP_SYS_ADMIN) :\n"
            "echo host | sudo tee /sys/class/usb_role/<id>/role\n"
            "# Reload xhci-platform / typec drivers if persistent.\n")


def _recipe_flapping() -> str:
    return ("# Transitional 'none' role visible — Type-C\n"
            "# controller is mid-flip. Re-poll in 2 s :\n"
            "watch -n2 cat /sys/class/usb_role/*/role\n"
            "# If persistent, suspect a flaky cable or PD chip.\n")


def _recipe_mismatch() -> str:
    return ("# Type-C data_role and usb_role disagree.\n"
            "for p in /sys/class/typec/port*; do\n"
            "  echo \"$p data=$(cat $p/data_role) \\\n"
            "    power=$(cat $p/power_role)\"\n"
            "done\n"
            "for r in /sys/class/usb_role/*; do\n"
            "  echo \"$r role=$(cat $r/role)\"\n"
            "done\n")
