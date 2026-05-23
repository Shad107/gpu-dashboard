"""Module edac_ecc_audit — DRAM ECC counters via EDAC (R&D #55.1).

Distinct from existing /sys/devices/system/machinecheck (MCE bank
state) — EDAC publishes **per-DIMM** correctable/uncorrectable
ECC counts that persist since boot. The two views complement each
other ; EDAC is the actionable per-stick layer.

Why this matters on an LLM rig :

* Uncorrectable ECC errors flip bits the kernel can't fix —
  on a host loading 30-GB GGUF weights into mmap'd memory, a
  single UE corrupts a layer's tensor silently, then the model
  starts emitting gibberish.
* Climbing correctable counts on one DIMM/channel are the lead
  indicator of an UE on that stick within days/weeks.
* On many distros the EDAC driver isn't loaded by default ; the
  user *thinks* ECC is "working" because the platform is server-
  class, but `cat /sys/devices/system/edac/mc/*/ce_count` is empty.

Reads :
  /sys/devices/system/edac/mc/mc*/{ue_count, ce_count, mc_name,
                                       size_mb}
  /sys/devices/system/edac/mc/mc*/dimm*/{dimm_ue_count,
                                            dimm_ce_count,
                                            dimm_label,
                                            dimm_location,
                                            size}
  /sys/devices/system/edac/mc/mc*/csrow*/{ue_count, ce_count}
  /sys/devices/system/edac/mc/mc*/reset_counters  (existence only)

Verdicts (priority-ordered) :
  ue_present                 ≥1 uncorrectable error reported on
                             any controller / DIMM.
  ce_rising                  ≥1 DIMM with ce_count > 0 — single
                             stick lead indicator.
  edac_absent                /sys/devices/system/edac/mc not
                             present.
  driver_missing             /sys/devices/system/edac/mc empty —
                             the EDAC driver isn't loaded.
  ok                         counters present and all zero.
  unknown                    /sys/devices/system/edac unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "edac_ecc_audit"


_SYS_EDAC_MC = "/sys/devices/system/edac/mc"

_MC_DIR_RE = re.compile(r"^mc(\d+)$")
_DIMM_DIR_RE = re.compile(r"^dimm\d+$")
_CSROW_DIR_RE = re.compile(r"^csrow\d+$")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def list_controllers(sys_edac_mc: str = _SYS_EDAC_MC) -> List[dict]:
    """Enumerate /sys/devices/system/edac/mc/mc<N> controllers."""
    if not os.path.isdir(sys_edac_mc):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_edac_mc)):
        if not _MC_DIR_RE.match(name):
            continue
        d = os.path.join(sys_edac_mc, name)
        controller = {
            "id": name,
            "ue_count": _read_int(os.path.join(d, "ue_count")),
            "ce_count": _read_int(os.path.join(d, "ce_count")),
            "mc_name": _read(os.path.join(d, "mc_name")),
            "size_mb": _read_int(os.path.join(d, "size_mb")),
            "dimms": _list_dimms(d),
        }
        out.append(controller)
    return out


def _list_dimms(controller_dir: str) -> List[dict]:
    out: List[dict] = []
    if not os.path.isdir(controller_dir):
        return out
    for name in sorted(os.listdir(controller_dir)):
        if not _DIMM_DIR_RE.match(name):
            continue
        d = os.path.join(controller_dir, name)
        ue = _read_int(os.path.join(d, "dimm_ue_count"))
        ce = _read_int(os.path.join(d, "dimm_ce_count"))
        if ue is None and ce is None:
            continue
        out.append({
            "id": name,
            "ue_count": ue,
            "ce_count": ce,
            "label": _read(os.path.join(d, "dimm_label")),
            "location": _read(os.path.join(d, "dimm_location")),
            "size": _read_int(os.path.join(d, "size")),
        })
    return out


def classify(controllers: List[dict],
              edac_present: bool) -> dict:
    if not edac_present:
        return {"verdict": "edac_absent",
                "reason": ("/sys/devices/system/edac/mc is not "
                          "present — kernel built without EDAC or "
                          "no ECC-capable controller."),
                "recommendation": _recipe_check_kernel()}

    if not controllers:
        return {"verdict": "driver_missing",
                "reason": ("/sys/devices/system/edac/mc exists but "
                          "no mc<N> controllers found — EDAC driver "
                          "(e.g. ie31200_edac, amd64_edac, "
                          "skx_edac) isn't loaded."),
                "recommendation": _recipe_load_driver()}

    # 1) ue_present — any UE on any controller or DIMM
    ue_ctrls = [c for c in controllers
                  if (c.get("ue_count") or 0) > 0
                  or any((d.get("ue_count") or 0) > 0
                            for d in c.get("dimms", []))]
    if ue_ctrls:
        sample_dimms: List[str] = []
        for c in ue_ctrls:
            for d in c.get("dimms", []):
                if (d.get("ue_count") or 0) > 0:
                    lbl = d.get("label") or d["id"]
                    sample_dimms.append(
                        f"{c['id']}/{lbl}={d['ue_count']}")
        sample = ", ".join(sample_dimms[:3]) or \
                   ", ".join(c["id"] for c in ue_ctrls)
        return {"verdict": "ue_present",
                "reason": (f"{len(ue_ctrls)} controller(s) report "
                          f"uncorrectable ECC errors : {sample}. "
                          f"Bit flips are getting through."),
                "recommendation": _recipe_ue()}

    # 2) ce_rising — any DIMM has ce_count > 0
    ce_dimms = []
    for c in controllers:
        for d in c.get("dimms", []):
            if (d.get("ce_count") or 0) > 0:
                lbl = d.get("label") or d["id"]
                ce_dimms.append(
                    (f"{c['id']}/{lbl}", d["ce_count"]))
    if ce_dimms:
        sample = ", ".join(f"{name}={n}" for name, n in
                              sorted(ce_dimms,
                                       key=lambda x: -x[1])[:3])
        return {"verdict": "ce_rising",
                "reason": (f"{len(ce_dimms)} DIMM(s) have "
                          f"non-zero correctable counts : "
                          f"{sample}. Replace the worst stick "
                          f"before it goes uncorrectable."),
                "recommendation": _recipe_ce()}

    return {"verdict": "ok",
            "reason": (f"{len(controllers)} EDAC controller(s) "
                      f"present, all counters zero."),
            "recommendation": ""}


def status(config=None, sys_edac_mc: str = _SYS_EDAC_MC) -> dict:
    edac_present = os.path.isdir(sys_edac_mc)
    controllers = list_controllers(sys_edac_mc)
    ok = edac_present
    verdict = classify(controllers, edac_present)
    return {"ok": ok,
              "controller_count": len(controllers),
              "controllers": controllers,
              "edac_present": edac_present,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_check_kernel() -> str:
    return ("# Check that the kernel was built with EDAC :\n"
            "grep -E 'CONFIG_EDAC=|CONFIG_EDAC_DECODE_MCE=' /boot/config-$(uname -r)\n"
            "# If absent, the platform either lacks ECC support or\n"
            "# the BIOS exposes it via a different mechanism — check\n"
            "# /sys/devices/system/machinecheck for MCE bank state.\n")


def _recipe_load_driver() -> str:
    return ("# Auto-detect and load the right EDAC driver :\n"
            "sudo modprobe edac_core\n"
            "# Pick the driver matching your CPU family :\n"
            "#   amd64_edac        (AMD K8/Family 10h-19h)\n"
            "#   skx_edac          (Intel Skylake-X / Cascade Lake)\n"
            "#   ie31200_edac      (Intel desktop)\n"
            "sudo modprobe amd64_edac  # or appropriate driver\n"
            "ls /sys/devices/system/edac/mc/\n")


def _recipe_ue() -> str:
    return ("# Identify the affected DIMM and plan replacement :\n"
            "grep -H . /sys/devices/system/edac/mc/mc*/dimm*/dimm_{ue_count,label,location}\n"
            "# UE counts on a specific stick → replace that DIMM\n"
            "# (LLM workloads on a UE host will silently corrupt\n"
            "# weights / KV-cache).\n")


def _recipe_ce() -> str:
    return ("# A DIMM is throwing correctable errors — lead indicator\n"
            "# of an uncorrectable failure within days :\n"
            "grep -H . /sys/devices/system/edac/mc/mc*/dimm*/dimm_{ce_count,label,location}\n"
            "# Reset the counters before any reboot diagnostic :\n"
            "echo 1 | sudo tee /sys/devices/system/edac/mc/mc*/reset_counters 2>/dev/null\n")
