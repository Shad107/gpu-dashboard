"""Module mei_hdcp_pxp_audit — Intel ME HDCP + PXP subclasses (R&D #64.2).

Distinct from R&D #62.2 mei_intel_me_audit (which walks
/sys/class/mei — the main ME firmware interface). This module
targets the *content protection* MEI subclasses :

  /sys/class/mei_hdcp   HDCP (High-bandwidth Digital Content
                          Protection) MEI clients — used by DRM
                          for protected display output.
  /sys/class/mei_pxp    Protected Xe Path (Intel Arc / Xe-LPG) —
                          required for hardware-accelerated
                          protected video decode on Intel
                          discrete GPUs.

Why this matters on a homelab LLM rig that *also* runs media :

* Intel Arc A380 / A750 / Battlemage discrete GPU with mei_pxp
  in `state=disabled` silently falls back to SW-only video
  decode. User attributes the slowness to "GPU broken."
* HDCP FW mismatch → display goes black on protected streams,
  often confused with cable/dongle issue.

Reads :
  /sys/class/mei_hdcp/<name>/{state, fw_status, fw_ver, hbm_ver}
  /sys/class/mei_pxp/<name>/{state, fw_status, fw_ver, hbm_ver}

Verdicts (priority-ordered) :
  pxp_disabled_with_gpu        ≥1 mei_pxp client with state !=
                               enabled AND an Intel discrete GPU
                               (PCI vendor 0x8086, base class
                               0x03) is present.
  hdcp_fw_mismatch             ≥1 mei_hdcp client with fw_ver
                               present but state != enabled.
  subclasses_no_consumer       Subclasses present, all disabled,
                               no consumer running.
  ok                           Subclasses healthy or absent on
                               a non-Intel-GPU host.
  unknown                      Both /sys/class/mei_hdcp + mei_pxp
                               absent (typical AMD-only / VM
                               host).

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "mei_hdcp_pxp_audit"


_SYS_MEI_HDCP = "/sys/class/mei_hdcp"
_SYS_MEI_PXP = "/sys/class/mei_pxp"
_SYS_BUS_PCI = "/sys/bus/pci/devices"


_INTEL_VENDOR = "0x8086"
_DISPLAY_BASE_CLASS = 0x03


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def list_subclass(sys_dir: str) -> List[dict]:
    if not os.path.isdir(sys_dir):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_dir)):
        d = os.path.join(sys_dir, name)
        if not os.path.isdir(d):
            continue
        out.append({
            "id": name,
            "state": _read(os.path.join(d, "state")),
            "fw_status": _read(os.path.join(d, "fw_status")),
            "fw_ver": _read(os.path.join(d, "fw_ver")),
            "hbm_ver": _read(os.path.join(d, "hbm_ver")),
        })
    return out


def has_intel_discrete_gpu(sys_bus_pci: str = _SYS_BUS_PCI
                              ) -> List[str]:
    """Return BDFs of Intel display-class PCI devices."""
    if not os.path.isdir(sys_bus_pci):
        return []
    out: List[str] = []
    for bdf in sorted(os.listdir(sys_bus_pci)):
        ddir = os.path.join(sys_bus_pci, bdf)
        vendor = _read(os.path.join(ddir, "vendor"))
        klass = _read(os.path.join(ddir, "class"))
        if vendor != _INTEL_VENDOR or not klass:
            continue
        try:
            base = (int(klass, 16) >> 16) & 0xff
        except ValueError:
            continue
        if base == _DISPLAY_BASE_CLASS:
            out.append(bdf)
    return out


def classify(hdcp: List[dict], pxp: List[dict],
              intel_gpus: List[str]) -> dict:
    if not hdcp and not pxp:
        return {"verdict": "unknown",
                "reason": ("Both /sys/class/mei_hdcp and "
                          "/sys/class/mei_pxp absent — typical "
                          "AMD-only / VM host, or kernel built "
                          "without those drivers."),
                "recommendation": ""}

    # 1) pxp_disabled_with_gpu
    if intel_gpus:
        bad_pxp = [p for p in pxp
                      if (p.get("state") or "").lower() !=
                         "enabled"]
        if bad_pxp:
            sample = ", ".join(
                f"{p['id']}({p.get('state')})"
                for p in bad_pxp[:3])
            return {"verdict": "pxp_disabled_with_gpu",
                    "reason": (f"{len(bad_pxp)} mei_pxp client(s) "
                              f"not enabled while Intel GPU "
                              f"present ({intel_gpus[0]}) : "
                              f"{sample}. HW-accel protected "
                              f"video decode unavailable."),
                    "recommendation": _recipe_pxp()}

    # 2) hdcp_fw_mismatch
    bad_hdcp = [h for h in hdcp
                   if h.get("fw_ver") and
                      (h.get("state") or "").lower() != "enabled"]
    if bad_hdcp:
        sample = ", ".join(
            f"{h['id']}(state={h.get('state')})"
            for h in bad_hdcp[:3])
        return {"verdict": "hdcp_fw_mismatch",
                "reason": (f"{len(bad_hdcp)} mei_hdcp client(s) "
                          f"with fw_ver set but state != enabled "
                          f": {sample}."),
                "recommendation": _recipe_hdcp()}

    # 3) subclasses_no_consumer
    all_subs = hdcp + pxp
    if all_subs and all((s.get("state") or "").lower() !=
                            "enabled" for s in all_subs):
        return {"verdict": "subclasses_no_consumer",
                "reason": (f"{len(all_subs)} subclass MEI "
                          f"client(s) present but none enabled — "
                          f"no consumer (DRM / media driver) "
                          f"bound."),
                "recommendation": _recipe_no_consumer()}

    return {"verdict": "ok",
            "reason": (f"{len(hdcp)} mei_hdcp + {len(pxp)} mei_pxp "
                      f"client(s), healthy."),
            "recommendation": ""}


def status(config=None,
            sys_mei_hdcp: str = _SYS_MEI_HDCP,
            sys_mei_pxp: str = _SYS_MEI_PXP,
            sys_bus_pci: str = _SYS_BUS_PCI) -> dict:
    hdcp = list_subclass(sys_mei_hdcp)
    pxp = list_subclass(sys_mei_pxp)
    intel_gpus = has_intel_discrete_gpu(sys_bus_pci)
    ok = bool(hdcp or pxp)
    verdict = classify(hdcp, pxp, intel_gpus)
    return {"ok": ok,
              "hdcp_count": len(hdcp),
              "hdcp_clients": hdcp,
              "pxp_count": len(pxp),
              "pxp_clients": pxp,
              "intel_display_gpus": intel_gpus,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_pxp() -> str:
    return ("# Load the PXP module (Intel Arc / Xe-LPG protected\n"
            "# media decode) :\n"
            "sudo modprobe mei_pxp\n"
            "dmesg | grep -i 'mei_pxp\\|i915\\|xe' | tail\n"
            "# Verify : cat /sys/class/mei_pxp/*/state\n")


def _recipe_hdcp() -> str:
    return ("# Reload HDCP MEI client :\n"
            "sudo modprobe -r mei_hdcp && sudo modprobe mei_hdcp\n"
            "dmesg | grep -i hdcp | tail\n")


def _recipe_no_consumer() -> str:
    return ("# Subclasses present but no consumer. Make sure the\n"
            "# DRM / media driver is loaded (i915 / xe / amdgpu) :\n"
            "lsmod | grep -E 'i915|xe|amdgpu'\n"
            "# If the consumer is loaded but state is still\n"
            "# 'disconnected', try :\n"
            "sudo systemctl restart systemd-modules-load\n")
