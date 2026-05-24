"""Module cpu_smt_control_audit — SMT over-mitigation +
SMT-off-still-vulnerable detector (R&D #87.3).

Two existing modules already cover most of the SMT /
vulnerabilities surface :

  * smt_audit (R&D #35.4) — toggle + offline-core
  * cpu_vulnerabilities_audit (R&D #53.2) — mitigations
    state + smt_forced_on_with_vuln

This audit owns the two remaining corner signals that are
genuinely not covered :

  * SMT explicitly disabled but vulnerability files STILL
    report "SMT vulnerable" — kernel / microcode update
    needed for the off-path mitigation to fully engage.
  * SMT explicitly disabled AND no relevant vulnerability
    mentions SMT — the user is paying a 30-50 % perf tax
    for security they don't actually need on a single-
    user homelab.

Reads :

  /sys/devices/system/cpu/smt/control          on / off /
                                               forceoff /
                                               notsupported
  /sys/devices/system/cpu/smt/active           0 / 1
  /sys/devices/system/cpu/vulnerabilities/*    Mitigation:
                                               / Vulnerable:
                                               strings

Vulnerability files inspected (the SMT-relevant subset) :
  l1tf, mds, taa, mmio_stale_data,
  spectre_v2, spec_store_bypass, srbds

Verdicts (worst first) :

  smt_off_still_vulnerable    SMT control = off / forceoff
                              AND any inspected vuln file
                              contains "SMT vulnerable" —
                              kernel/microcode update
                              needed.
  smt_off_over_mitigated      SMT off AND none of the SMT-
                              relevant vulns flagged the
                              hardware as vulnerable —
                              perf tax for no security
                              benefit on this CPU.
  ok                          SMT state coherent with
                              vulnerability profile.
  requires_root               vulnerability files
                              unreadable.
  unknown                     no /sys/devices/system/cpu/
                              smt/control file at all
                              (notsupported or older
                              kernel).
"""
from __future__ import annotations

import os
from typing import Optional

DEFAULT_SMT_ROOT = "/sys/devices/system/cpu/smt"
DEFAULT_VULN_ROOT = "/sys/devices/system/cpu/vulnerabilities"

# Subset of vulnerability files whose mitigation status
# depends on SMT state. (Others — spectre_v1, meltdown,
# itlb_multihit — are SMT-orthogonal and we ignore them.)
_SMT_RELEVANT_VULNS = (
    "l1tf", "mds", "taa", "mmio_stale_data",
    "spectre_v2", "spec_store_bypass", "srbds",
)

# SMT control values that mean "SMT is off".
_SMT_OFF_VALUES = {"off", "forceoff"}


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def read_smt_state(root: str = DEFAULT_SMT_ROOT) -> dict:
    return {
        "control": _read_text(
            os.path.join(root, "control")) or "",
        "active": _read_text(
            os.path.join(root, "active")) or "",
    }


def read_vulns(root: str = DEFAULT_VULN_ROOT) -> dict:
    out: dict = {}
    if not os.path.isdir(root):
        return out
    for name in _SMT_RELEVANT_VULNS:
        v = _read_text(os.path.join(root, name))
        if v is not None:
            out[name] = v
    return out


def _has_smt_vulnerable_text(vuln_text: str) -> bool:
    """Return True if the kernel string indicates the SMT
    aspect of this vuln is NOT mitigated."""
    if not vuln_text:
        return False
    # Standard kernel phrasings :
    #   "Mitigation: ...; SMT vulnerable"
    #   "Mitigation: ...; SMT Host state unknown"
    #   "Vulnerable; SMT vulnerable"
    text_lc = vuln_text.lower()
    return ("smt vulnerable" in text_lc
            or "smt host state unknown" in text_lc)


def classify(smt: dict, vulns: dict,
             vulns_present: bool) -> dict:
    control = smt.get("control") or ""
    if not control:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/devices/system/cpu/smt/control "
                    "absent — kernel reports SMT not "
                    "supported on this hardware.")}
    if control == "notsupported":
        return {"verdict": "unknown",
                "reason": (
                    "SMT control reports 'notsupported' "
                    "— CPU has no SMT (VM, non-SMT die, "
                    "or fused-off on a workstation chip).")}
    if not vulns_present:
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/devices/system/cpu/vulnerabilities "
                    "absent or unreadable — re-run as root "
                    "for full SMT-vs-vuln cross-check.")}

    smt_off = control in _SMT_OFF_VALUES

    if smt_off:
        # 1. warn — SMT off but kernel STILL says vulnerable
        offenders = {
            name: text for name, text in vulns.items()
            if _has_smt_vulnerable_text(text)}
        if offenders:
            first_name = sorted(offenders)[0]
            return {
                "verdict": "smt_off_still_vulnerable",
                "reason": (
                    f"SMT control = {control} but "
                    f"{len(offenders)} vuln file(s) still "
                    f"report SMT vulnerable (first: "
                    f"{first_name}). Update kernel + "
                    "microcode."),
                "control": control,
                "vulnerable": sorted(offenders.keys())}

        # 2. accent — SMT off but no SMT-relevant vulns
        # mention this CPU. Check if ANY vuln on this CPU is
        # flagged "Vulnerable" (otherwise mitigation is
        # complete and SMT-off is purely a perf tax).
        any_vulnerable = any(
            text.startswith("Vulnerable")
            for text in vulns.values())
        if not any_vulnerable:
            return {"verdict": "smt_off_over_mitigated",
                    "reason": (
                        f"SMT control = {control} on a CPU "
                        "with no SMT-relevant vulnerability "
                        "active — paying a perf tax for "
                        "security you don't actually need."),
                    "control": control}

    return {"verdict": "ok",
            "reason": (
                f"SMT control = {control} ; "
                f"{len(vulns)} SMT-relevant vuln file(s) "
                "inspected, profile coherent.")}


def status(config: Optional[dict] = None,
           smt_root: str = DEFAULT_SMT_ROOT,
           vuln_root: str = DEFAULT_VULN_ROOT) -> dict:
    smt = read_smt_state(smt_root)
    vulns = read_vulns(vuln_root)
    vulns_present = bool(vulns)
    verdict = classify(smt, vulns, vulns_present)
    return {
        "ok": verdict["verdict"] not in (
            "smt_off_still_vulnerable",
            "requires_root", "unknown"),
        "smt_control": smt.get("control"),
        "smt_active": smt.get("active"),
        "vulns_inspected": len(vulns),
        "verdict": verdict,
    }
