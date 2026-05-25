"""Module kvm_mmu_audit — KVM MMU + NX-huge + EPT/NPT
posture (R&D #97.1).

kvm_misc_audit (existing) covers halt_poll_ns + /dev/kvm
permissions + `nested` flag. It does NOT inspect the
MMU / EPT / NPT / NX-huge knobs that determine whether
guest VMs benefit from hardware-accelerated paging or
have iTLB-multihit mitigations engaged.

Reads :

  /sys/module/kvm/parameters/tdp_mmu                   Y/N
  /sys/module/kvm/parameters/nx_huge_pages             Y/N
  /sys/module/kvm/parameters/nx_huge_pages_recovery_ratio  int
  /sys/module/kvm_intel/parameters/ept                 Y/N
  /sys/module/kvm_amd/parameters/npt                   Y/N

Verdicts (worst-first) :

  nx_huge_pages_disabled   err   kvm/nx_huge_pages = N —
                                 iTLB multihit mitigation
                                 OFF. Any guest can downgrade
                                 to 4 KiB pages and DoS.
  ept_npt_off              warn  hw-accelerated guest paging
                                 disabled on the loaded kvm
                                 sub-module. Guests fall back
                                 to slow shadow paging.
  tdp_mmu_off              warn  kvm/tdp_mmu = N — legacy
                                 MMU slow path for huge guests.
  recovery_ratio_zero      accent NX-huge recovery_ratio = 0
                                 — once collapsed, kernel
                                 never re-promotes pages.
  ok                       MMU surface coherent.
  requires_root            params mode-400.
  unknown                  /sys/module/kvm absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "kvm_mmu_audit"

DEFAULT_KVM = "/sys/module/kvm/parameters"
DEFAULT_KVM_INTEL = "/sys/module/kvm_intel/parameters"
DEFAULT_KVM_AMD = "/sys/module/kvm_amd/parameters"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def _is_yes(text: Optional[str]) -> Optional[bool]:
    """KVM params are Y/N (with sometimes 0/1). Return None if
    unreadable, True for Y/1, False for N/0."""
    if text is None:
        return None
    t = text.strip().upper()
    if t in ("Y", "1", "TRUE"):
        return True
    if t in ("N", "0", "FALSE"):
        return False
    return None


def read_state(kvm: str = DEFAULT_KVM,
               kvm_intel: str = DEFAULT_KVM_INTEL,
               kvm_amd: str = DEFAULT_KVM_AMD) -> dict:
    return {
        "kvm_present": os.path.isdir(kvm),
        "tdp_mmu": _is_yes(
            _read_text(os.path.join(kvm, "tdp_mmu"))),
        "nx_huge_pages": _is_yes(
            _read_text(
                os.path.join(kvm, "nx_huge_pages"))),
        "nx_recovery_ratio": _read_int(
            os.path.join(
                kvm, "nx_huge_pages_recovery_ratio")),
        "intel_ept": _is_yes(
            _read_text(os.path.join(kvm_intel, "ept"))),
        "amd_npt": _is_yes(
            _read_text(os.path.join(kvm_amd, "npt"))),
        "intel_present": os.path.isdir(kvm_intel),
        "amd_present": os.path.isdir(kvm_amd),
    }


def classify(s: dict) -> dict:
    if not s["kvm_present"]:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/module/kvm absent — KVM module not "
                    "loaded (no virtualization support or "
                    "module not auto-loaded).")}

    if (s["tdp_mmu"] is None and s["nx_huge_pages"] is None):
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/module/kvm/parameters/* unreadable "
                    "— re-run as root.")}

    # err — NX huge pages mitigation off
    if s["nx_huge_pages"] is False:
        return {
            "verdict": "nx_huge_pages_disabled",
            "reason": (
                "kvm.nx_huge_pages = N — iTLB-multihit "
                "guest-DoS mitigation is OFF. A malicious "
                "guest can crash the host. Set "
                "nx_huge_pages=Y on cmdline or reload kvm "
                "with the parameter.")}

    # warn — EPT/NPT disabled (hw-accel paging off)
    if s["intel_present"] and s["intel_ept"] is False:
        return {
            "verdict": "ept_npt_off",
            "reason": (
                "kvm_intel.ept = N — hw-accelerated guest "
                "paging disabled. Guests fall back to "
                "shadow paging (~5-10x slower).")}
    if s["amd_present"] and s["amd_npt"] is False:
        return {
            "verdict": "ept_npt_off",
            "reason": (
                "kvm_amd.npt = N — hw-accelerated guest "
                "paging disabled. Guests fall back to "
                "shadow paging (~5-10x slower).")}

    # warn — TDP MMU off
    if s["tdp_mmu"] is False:
        return {
            "verdict": "tdp_mmu_off",
            "reason": (
                "kvm.tdp_mmu = N — legacy MMU slow path "
                "for huge guests. Re-enable for better "
                "VRAM-heavy guest performance.")}

    # accent — recovery_ratio zero
    rr = s["nx_recovery_ratio"]
    if rr is not None and rr == 0:
        return {
            "verdict": "recovery_ratio_zero",
            "reason": (
                "kvm.nx_huge_pages_recovery_ratio = 0 — "
                "once shattered, NX huge pages never "
                "rebuilt. Set to 60 for a sane reclaim "
                "cadence.")}

    return {"verdict": "ok",
            "reason": (
                f"KVM MMU coherent : tdp_mmu={s['tdp_mmu']}, "
                f"nx_huge_pages={s['nx_huge_pages']}, "
                f"ept={s['intel_ept']}, npt={s['amd_npt']}.")}


def status(config: Optional[dict] = None,
           kvm: str = DEFAULT_KVM,
           kvm_intel: str = DEFAULT_KVM_INTEL,
           kvm_amd: str = DEFAULT_KVM_AMD) -> dict:
    s = read_state(kvm, kvm_intel, kvm_amd)
    verdict = classify(s)
    return {
        "ok": verdict["verdict"] == "ok",
        "kvm_present": s["kvm_present"],
        "tdp_mmu": s["tdp_mmu"],
        "nx_huge_pages": s["nx_huge_pages"],
        "intel_ept": s["intel_ept"],
        "amd_npt": s["amd_npt"],
        "verdict": verdict,
    }
