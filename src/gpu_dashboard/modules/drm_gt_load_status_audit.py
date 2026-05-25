"""Module drm_gt_load_status_audit — i915 / amdgpu GT firmware
load + engine wedge status (R&D #105.2).

Intel i915 (Gen12+) requires GuC + HuC firmware to schedule
compute and decode media. amdgpu has analogous PSP / SDMA
firmware blobs. When firmware load fails or an engine wedges
without bumping reset_count, OpenCL / level-zero / VAAPI /
compute contexts break *silently* — gpu_reset.py counts
reset_count only, gsp_status.py is NVIDIA-only.

Reads (best-effort, root-gated debugfs) :

  /sys/kernel/debug/dri/<N>/gt/uc/guc_info
  /sys/kernel/debug/dri/<N>/gt/uc/huc_info
  /sys/kernel/debug/dri/<N>/i915_gt_info
  /sys/class/drm/card*/device/vendor          (chip select)
  /sys/class/drm/card*/device/pp_features     (amdgpu)
  /sys/class/drm/card*/device/gpu_recovery    (amdgpu)

Vendor IDs : 0x8086 = Intel, 0x1002 = AMD, 0x10de = NVIDIA.
NVIDIA-only hosts return `unknown` — covered by gsp_status.

Verdicts (worst-first) :

  guc_not_loaded                err     Intel GT but GuC info
                                        says NOT LOADED — compute
                                        / level-zero broken.
  huc_load_failed               warn    HuC failed — video decode
                                        (HEVC, AV1) breaks.
  amdgpu_recovery_off           warn    amdgpu gpu_recovery=0 —
                                        GPU hangs won't auto-
                                        recover.
  firmware_version_mismatch     accent  Loaded vs requested
                                        firmware version differ.
  ok                                    GT firmware healthy.
  requires_root                         debugfs unreadable.
  unknown                               No Intel/AMD GPU.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "drm_gt_load_status_audit"

DEFAULT_DRM_CLASS = "/sys/class/drm"
DEFAULT_DEBUGFS_DRI = "/sys/kernel/debug/dri"

_VENDOR_INTEL = "0x8086"
_VENDOR_AMD = "0x1002"


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


def find_drm_cards(drm_class: str = DEFAULT_DRM_CLASS
                     ) -> list:
    """Return list of {card, vendor, vendor_id}."""
    out: list = []
    if not os.path.isdir(drm_class):
        return out
    try:
        ents = sorted(os.listdir(drm_class))
    except OSError:
        return out
    for ent in ents:
        if not (ent.startswith("card") and ent[4:].isdigit()):
            continue
        dev = os.path.join(drm_class, ent, "device")
        vendor_text = _read_text(
            os.path.join(dev, "vendor"))
        if vendor_text is None:
            continue
        vid = vendor_text.strip()
        if vid == _VENDOR_INTEL:
            vendor = "intel"
        elif vid == _VENDOR_AMD:
            vendor = "amd"
        else:
            vendor = "other"
        out.append({"card": ent, "vendor_id": vid,
                    "vendor": vendor,
                    "device_path": dev})
    return out


def parse_uc_info(text: Optional[str]) -> dict:
    """Parse i915 GuC/HuC info block.

    Looks for: 'status: <STATE>' and 'version: ...'.
    """
    out: dict = {"loaded": None, "version": None,
                 "raw_status": None}
    if not text:
        return out
    for line in text.splitlines():
        # 'status:' or 'fw status:' or 'GuC status:'
        m = re.search(r"status\s*:\s*(\S+)", line,
                       re.IGNORECASE)
        if m and out["raw_status"] is None:
            s = m.group(1).upper()
            out["raw_status"] = s
            out["loaded"] = s in (
                "RUNNING", "LOADED", "PROTECTED",
                "READY", "ENABLED")
        m2 = re.search(r"version\s*:\s*(\S+)", line,
                        re.IGNORECASE)
        if m2 and out["version"] is None:
            out["version"] = m2.group(1)
    return out


def classify(cards: list,
             intel_present: bool,
             amd_present: bool,
             debugfs_readable: bool,
             guc_info: dict,
             huc_info: dict,
             amd_recovery: Optional[int]) -> dict:
    if not cards or (not intel_present and not amd_present):
        return {"verdict": "unknown",
                "reason": (
                    "No Intel / AMD GT detected — module "
                    "doesn't apply (NVIDIA-only or "
                    "headless host).")}

    # err — Intel + GuC explicitly not loaded
    if intel_present and guc_info.get("loaded") is False:
        return {
            "verdict": "guc_not_loaded",
            "reason": (
                f"Intel GuC NOT LOADED "
                f"(status={guc_info.get('raw_status')}). "
                "OpenCL / level-zero / compute contexts "
                "will fail.")}

    # warn — HuC failed
    if intel_present and huc_info.get("loaded") is False:
        return {
            "verdict": "huc_load_failed",
            "reason": (
                f"Intel HuC NOT LOADED "
                f"(status={huc_info.get('raw_status')}). "
                "HEVC / AV1 hardware decode disabled.")}

    # warn — amdgpu recovery off
    if amd_present and amd_recovery == 0:
        return {
            "verdict": "amdgpu_recovery_off",
            "reason": (
                "amdgpu.gpu_recovery=0 — GPU hangs will "
                "NOT auto-recover. A single shader timeout "
                "freezes the desktop.")}

    # requires_root path : Intel/AMD present but debugfs
    # gave us nothing
    if (intel_present
            and guc_info.get("loaded") is None
            and not debugfs_readable):
        return {"verdict": "requires_root",
                "reason": (
                    "Intel GT detected but /sys/kernel/"
                    "debug/dri/* unreadable — re-run as "
                    "root for GuC / HuC status.")}

    return {"verdict": "ok",
            "reason": (
                f"{len(cards)} DRM card(s) detected ; "
                "GT firmware healthy where readable.")}


def status(config: Optional[dict] = None,
           drm_class: str = DEFAULT_DRM_CLASS,
           debugfs_dri: str = DEFAULT_DEBUGFS_DRI) -> dict:
    cards = find_drm_cards(drm_class)
    intel_present = any(c["vendor"] == "intel"
                        for c in cards)
    amd_present = any(c["vendor"] == "amd" for c in cards)

    debugfs_readable = (os.path.isdir(debugfs_dri)
                        and os.access(debugfs_dri,
                                      os.R_OK))

    guc_info: dict = {}
    huc_info: dict = {}
    amd_recovery: Optional[int] = None

    if intel_present and debugfs_readable:
        # Scan all dri/<N>/ dirs
        try:
            for ent in sorted(os.listdir(debugfs_dri)):
                gt_uc = os.path.join(
                    debugfs_dri, ent, "gt", "uc")
                g = parse_uc_info(_read_text(
                    os.path.join(gt_uc, "guc_info")))
                if g.get("raw_status"):
                    guc_info = g
                h = parse_uc_info(_read_text(
                    os.path.join(gt_uc, "huc_info")))
                if h.get("raw_status"):
                    huc_info = h
        except OSError:
            pass

    if amd_present:
        for c in cards:
            if c["vendor"] != "amd":
                continue
            v = _read_int(os.path.join(
                c["device_path"], "gpu_recovery"))
            if v is not None:
                amd_recovery = v
                break

    verdict = classify(
        cards, intel_present, amd_present,
        debugfs_readable, guc_info, huc_info, amd_recovery)

    return {
        "ok": verdict["verdict"] == "ok",
        "card_count": len(cards),
        "intel_present": intel_present,
        "amd_present": amd_present,
        "guc_status": guc_info.get("raw_status"),
        "huc_status": huc_info.get("raw_status"),
        "amdgpu_recovery": amd_recovery,
        "verdict": verdict,
    }
