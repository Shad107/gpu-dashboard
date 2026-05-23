"""Module edac_dimm_ce_trend_audit — per-DIMM EDAC correctable /
uncorrectable error counter audit (R&D #71.4).

The kernel's EDAC (Error Detection And Correction) framework
exposes per-DIMM CE/UE counters under :

  /sys/devices/system/edac/mc/mc<N>/dimm<M>/
      dimm_ce_count   monotonic correctable-error counter
      dimm_ue_count   monotonic uncorrectable-error counter
      dimm_label      vendor label ("CPU_SrcID#0_Ha#0_Chan#0...")
      size_mb         DIMM capacity in MiB

Existing edac_ecc_audit covers the top-level controller and
overall CE/UE; this audit drills into PER-DIMM counters so a
single failing DIMM (the one that wants replacing) can be
identified by `dimm_label` instead of just "MC0 reports
errors."

Distinguishes:
  * UE on any DIMM       → catastrophic, replace immediately.
  * CE > 0 on a DIMM    → DIMM scrubbing kicked in ; usually
                          early-warning of failing module
                          (counts in the thousands ≥1 hour =
                          actionable).
  * Steady CE = 0      → healthy.

"Rising" detection (one count this hour vs next hour) needs
state persistence which isn't available in a single-shot audit
— the rising/steady distinction is therefore based on absolute
counter magnitude alone.

Verdicts (priority order) :
  dimm_ue_present              ≥1 DIMM has dimm_ue_count > 0.
  dimm_ce_rising               ≥1 DIMM has CE count ≥ 1000
                                 (treated as actionable
                                 magnitude).
  dimm_ce_nonzero_steady       ≥1 DIMM has CE count > 0 but
                                 < 1000 (mild scrubbing).
  edac_unsupported             /sys/devices/system/edac/mc/mc*
                                 absent (KVM guest, EDAC driver
                                 not loaded).
  ok                            all DIMM counters zero.
  unknown                       /sys/devices/system/edac/mc
                                 absent entirely.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "edac_dimm_ce_trend_audit"


_SYS_EDAC_MC = "/sys/devices/system/edac/mc"

_MC_RE = re.compile(r"^mc\d+$")
_DIMM_RE = re.compile(r"^dimm\d+$")

_CE_RISING_THRESHOLD = 1000


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def list_dimms(sys_edac: str = _SYS_EDAC_MC) -> List[dict]:
    if not os.path.isdir(sys_edac):
        return []
    out: List[dict] = []
    try:
        mcs = sorted(os.listdir(sys_edac))
    except OSError:
        return []
    for mc in mcs:
        if not _MC_RE.match(mc):
            continue
        mc_dir = os.path.join(sys_edac, mc)
        if not os.path.isdir(mc_dir):
            continue
        try:
            dimms = sorted(os.listdir(mc_dir))
        except OSError:
            continue
        for dn in dimms:
            if not _DIMM_RE.match(dn):
                continue
            d = os.path.join(mc_dir, dn)
            if not os.path.isdir(d):
                continue
            out.append({
                "mc": mc,
                "dimm": dn,
                "label": _read(os.path.join(d, "dimm_label")),
                "size_mb": _read_int(os.path.join(
                    d, "size_mb")),
                "ce_count": _read_int(os.path.join(
                    d, "dimm_ce_count")),
                "ue_count": _read_int(os.path.join(
                    d, "dimm_ue_count")),
            })
    return out


def classify(dimms: List[dict], edac_present: bool,
              mc_count: int) -> dict:
    if not edac_present:
        return {"verdict": "unknown",
                "reason": ("/sys/devices/system/edac/mc absent — "
                          "EDAC subsystem not built into kernel."),
                "recommendation": ""}

    if mc_count == 0:
        return {"verdict": "edac_unsupported",
                "reason": ("/sys/devices/system/edac/mc present "
                          "but no memory controllers exposed — "
                          "ECC-less RAM, KVM guest, or driver "
                          "not loaded."),
                "recommendation": _recipe_unsupported()}

    # 1) dimm_ue_present
    ue = [d for d in dimms if (d.get("ue_count") or 0) > 0]
    if ue:
        sample = ", ".join(
            f"{d['mc']}/{d['dimm']} ({d.get('label') or '?'}) "
            f"UE={d['ue_count']}"
                for d in ue[:3])
        return {"verdict": "dimm_ue_present",
                "reason": (f"{len(ue)} DIMM(s) report "
                          f"uncorrectable errors : {sample}. "
                          f"Replace immediately."),
                "recommendation": _recipe_ue()}

    # 2) dimm_ce_rising — CE count above threshold
    rising = [d for d in dimms
                  if (d.get("ce_count") or 0)
                      >= _CE_RISING_THRESHOLD]
    if rising:
        sample = ", ".join(
            f"{d['mc']}/{d['dimm']} ({d.get('label') or '?'}) "
            f"CE={d['ce_count']}"
                for d in rising[:3])
        return {"verdict": "dimm_ce_rising",
                "reason": (f"{len(rising)} DIMM(s) with CE "
                          f">= {_CE_RISING_THRESHOLD} : "
                          f"{sample}."),
                "recommendation": _recipe_ce_rising()}

    # 3) dimm_ce_nonzero_steady
    nonzero = [d for d in dimms
                  if (d.get("ce_count") or 0) > 0]
    if nonzero:
        sample = ", ".join(
            f"{d['mc']}/{d['dimm']} CE={d['ce_count']}"
                for d in nonzero[:3])
        return {"verdict": "dimm_ce_nonzero_steady",
                "reason": (f"{len(nonzero)} DIMM(s) report "
                          f"non-zero CE (below "
                          f"{_CE_RISING_THRESHOLD}) : "
                          f"{sample}."),
                "recommendation": _recipe_ce_steady()}

    return {"verdict": "ok",
            "reason": (f"All {len(dimms)} DIMM(s) report CE=0 "
                      f"and UE=0."),
            "recommendation": ""}


def status(config=None,
            sys_edac: str = _SYS_EDAC_MC) -> dict:
    edac_present = os.path.isdir(sys_edac)
    dimms = list_dimms(sys_edac)
    mc_count = len({d["mc"] for d in dimms})
    verdict = classify(dimms, edac_present, mc_count)
    return {"ok": edac_present,
              "edac_present": edac_present,
              "mc_count": mc_count,
              "dimm_count": len(dimms),
              "dimms": dimms,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_unsupported() -> str:
    return ("# No memory controllers exposed. Common reasons :\n"
            "#   - non-ECC consumer DIMMs (no per-DIMM counters)\n"
            "#   - EDAC driver not loaded ; on Intel try :\n"
            "sudo modprobe i7core_edac sb_edac skx_edac\n"
            "# On AMD :\n"
            "sudo modprobe amd64_edac amd64_edac_mod\n")


def _recipe_ue() -> str:
    return ("# Uncorrectable errors on a DIMM are catastrophic.\n"
            "# Locate the failing module by label :\n"
            "for d in /sys/devices/system/edac/mc/mc*/dimm*; do\n"
            "  echo \"$d $(cat $d/dimm_label) "
            "UE=$(cat $d/dimm_ue_count)\"\n"
            "done | awk '$3>0'\n"
            "# Reboot, run memtest86+, replace flagged DIMM.\n")


def _recipe_ce_rising() -> str:
    return ("# CE count above 1k = scrubbing is busy ; this DIMM\n"
            "# is on borrowed time. Plan replacement :\n"
            "for d in /sys/devices/system/edac/mc/mc*/dimm*; do\n"
            "  echo \"$(cat $d/dimm_label) CE=$(cat $d/dimm_ce_count)\"\n"
            "done\n"
            "# Cross-reference with dmesg for syndrome data :\n"
            "sudo dmesg | grep -iE 'edac|mce' | tail\n")


def _recipe_ce_steady() -> str:
    return ("# Low non-zero CE count : informational ; monitor.\n"
            "# Watch trend over time :\n"
            "watch -n60 \"grep . /sys/devices/system/edac/mc/\\\n"
            "  mc*/dimm*/dimm_ce_count\"\n")
