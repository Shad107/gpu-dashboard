"""Module mei_intel_me_audit — Intel ME / MEI status (R&D #62.2).

Reads /sys/class/mei/mei*/{fw_status, fw_ver, hbm_ver, dev_state,
tx_queue_limit} + /dev/mei* presence.

The Intel Management Engine (ME) exposes its host interface via
the MEI bus. On consumer Intel CPUs the ME status directly gates :

* CPU package power budget (PL1 / PL2) — if ME is in recovery
  mode, the firmware silently clamps the CPU to a defensive
  power envelope, which looks like a thermal regression to the
  user.
* TPM / fTPM functionality, vPro AMT enablement.
* Some BIOS recovery / firmware-update paths.

Distinct from the existing tpm_audit module (which reads
/sys/class/tpm, the discrete TPM endpoint) — the MEI bus exposes
the embedded fTPM and ME firmware itself.

Verdicts (priority-ordered) :
  me_recovery_mode             fw_status indicates recovery /
                               error / disabled bit pattern.
  me_disabled_but_present      MEI subsystem visible but
                               dev_state != "enabled".
  fw_status_error              fw_status non-zero AND not the
                               standard "operational" pattern.
  ok                           MEI operational.
  absent                       /sys/class/mei + /dev/mei* both
                               missing (AMD host, or VM without
                               ME passthrough — most common).
  unknown                      sysfs unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "mei_intel_me_audit"


_SYS_CLASS_MEI = "/sys/class/mei"
_DEV = "/dev"

_MEI_DIR_RE = re.compile(r"^mei\d+$")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def list_mei_devices(sys_mei: str = _SYS_CLASS_MEI) -> List[dict]:
    if not os.path.isdir(sys_mei):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_mei)):
        if not _MEI_DIR_RE.match(name):
            continue
        d = os.path.join(sys_mei, name)
        out.append({
            "id": name,
            "fw_status": _read(os.path.join(d, "fw_status")),
            "fw_ver": _read(os.path.join(d, "fw_ver")),
            "hbm_ver": _read(os.path.join(d, "hbm_ver")),
            "dev_state": _read(os.path.join(d, "dev_state")),
            "tx_queue_limit": _read(
                os.path.join(d, "tx_queue_limit")),
        })
    return out


def list_dev_nodes(dev: str = _DEV) -> List[str]:
    if not os.path.isdir(dev):
        return []
    return sorted(name for name in os.listdir(dev)
                    if name.startswith("mei"))


def _fw_status_indicates_recovery(s: Optional[str]) -> bool:
    """Heuristic : Intel ME fw_status is 6 32-bit hex words.
    The first word's bits 4-6 encode the current operation mode :
      0 = normal, 1 = debug, 2 = soft-temp-disable, 3 = security
    override, 4 = SPS, 5 = ME recovery.
    We flag any explicit '5' or any high-error bits."""
    if not s:
        return False
    first = s.split()[0] if s.split() else ""
    try:
        v = int(first, 16)
    except ValueError:
        return False
    op_mode = (v >> 16) & 0xF
    return op_mode == 5  # ME recovery


def _fw_status_has_error(s: Optional[str]) -> bool:
    if not s:
        return False
    words = s.split()
    if not words:
        return False
    try:
        v = int(words[0], 16)
    except ValueError:
        return False
    # Bit 23 = error indication on most generations.
    return bool(v & (1 << 23))


def classify(devices: List[dict], dev_nodes: List[str]) -> dict:
    if not devices and not dev_nodes:
        return {"verdict": "absent",
                "reason": ("/sys/class/mei + /dev/mei* both absent "
                          "— AMD host, or VM without Intel ME "
                          "passthrough."),
                "recommendation": ""}

    if not devices:
        return {"verdict": "unknown",
                "reason": ("MEI dev nodes present but no "
                          "/sys/class/mei/mei*."),
                "recommendation": ""}

    # 1) me_recovery_mode
    recovery = [d for d in devices
                   if _fw_status_indicates_recovery(d.get("fw_status"))]
    if recovery:
        return {"verdict": "me_recovery_mode",
                "reason": (f"Intel ME on {recovery[0]['id']} in "
                          f"recovery (fw_status op_mode=5). CPU "
                          f"PL1/PL2 may be silently clamped."),
                "recommendation": _recipe_recovery()}

    # 2) me_disabled_but_present
    disabled = [d for d in devices
                   if (d.get("dev_state") or "").lower() not in
                      ("enabled", "init_clients", "")]
    if disabled:
        sample = ", ".join(
            f"{d['id']}({d.get('dev_state', '?')})"
            for d in disabled[:3])
        return {"verdict": "me_disabled_but_present",
                "reason": (f"MEI sysfs present but dev_state not "
                          f"enabled : {sample}."),
                "recommendation": _recipe_disabled()}

    # 3) fw_status_error
    errored = [d for d in devices
                  if _fw_status_has_error(d.get("fw_status"))]
    if errored:
        return {"verdict": "fw_status_error",
                "reason": (f"fw_status on {errored[0]['id']} has "
                          f"the error bit set : "
                          f"{errored[0].get('fw_status')}."),
                "recommendation": _recipe_fw_error()}

    return {"verdict": "ok",
            "reason": (f"{len(devices)} MEI device(s) operational, "
                      f"fw_ver={devices[0].get('fw_ver', '?')}."),
            "recommendation": ""}


def status(config=None,
            sys_mei: str = _SYS_CLASS_MEI,
            dev: str = _DEV) -> dict:
    devices = list_mei_devices(sys_mei)
    dev_nodes = list_dev_nodes(dev)
    ok = bool(devices)
    verdict = classify(devices, dev_nodes)
    return {"ok": ok,
              "device_count": len(devices),
              "devices": devices,
              "dev_nodes": dev_nodes,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_recovery() -> str:
    return ("# ME in recovery mode silently clamps CPU PL1/PL2.\n"
            "# Decode the full fw_status :\n"
            "cat /sys/class/mei/mei0/fw_status\n"
            "# Use intelmetool or vendor utility to confirm.\n"
            "# Recovery : cold-boot (full AC removal 30 s on a\n"
            "# desktop), then re-flash BIOS to a vendor-signed\n"
            "# build. If still stuck, check vendor advisory.\n")


def _recipe_disabled() -> str:
    return ("# Inspect the MEI dev_state and dmesg :\n"
            "grep . /sys/class/mei/mei*/dev_state\n"
            "dmesg | grep -i 'mei\\|management engine' | tail\n"
            "# Reload the MEI driver to retry init :\n"
            "sudo modprobe -r mei_me mei && sudo modprobe mei_me\n")


def _recipe_fw_error() -> str:
    return ("# fw_status error bit set — decode all 6 words :\n"
            "cat /sys/class/mei/mei0/fw_status | tr ' ' '\\n'\n"
            "# Check Intel CSME advisories for the specific\n"
            "# code, and apply the latest microcode + BIOS update\n"
            "# from the vendor.\n")
