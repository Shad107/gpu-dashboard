"""Module edac_ram_ecc — system RAM ECC error counters (R&D #41.2).

ECC RAM only helps when the kernel is *reporting* the corrected /
uncorrectable errors it sees. The EDAC subsystem exposes per-
memory-controller counters at /sys/devices/system/edac/mc/mc*/ :

  ce_count                  total corrected (single-bit) errors
                            across the controller — a small
                            non-zero count is normal cosmic-ray
                            noise ; climbing fast = DIMM failing.
  ue_count                  total uncorrectable (multi-bit) errors
                            — any non-zero count is *critical*,
                            implies silent data corruption already.
  mc_name                   driver name (e.g. "amd64_edac_mod",
                            "skx_edac") — empty if EDAC not
                            populated for this CPU.
  dimm0/dimm_ce_count       per-DIMM corrected-error counter
  dimm0/dimm_ue_count       per-DIMM uncorrectable counter
  dimm0/dimm_label          BIOS-provided slot label (e.g. CPU0_A1)
  dimm0/size_mb             DIMM capacity (helps find the "this
                            error came from the 32 GB CPU0_DIMM_A2
                            stick" pointer for replacement).

Verdicts :
  ecc_clean              ECC enabled (mc_name set), zero errors.
  ce_climbing            ce_count > 0 on any DIMM — flag for
                         monitoring, not immediate replacement.
  ue_present             ANY ue_count > 0 — data corruption has
                         already happened. CRITICAL.
  ecc_disabled           No /sys/devices/system/edac/mc/* dirs at
                         all — either non-ECC RAM, ECC turned off
                         in BIOS, or kernel module not loaded
                         (amd64_edac / i7core_edac / etc).
  unknown                /sys/devices/system/edac unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "edac_ram_ecc"


_SYS_EDAC_MC = "/sys/devices/system/edac/mc"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def list_controllers(sys_mc: str = _SYS_EDAC_MC) -> list:
    if not os.path.isdir(sys_mc):
        return []
    return sorted(n for n in os.listdir(sys_mc)
                    if n.startswith("mc")
                    and os.path.isdir(os.path.join(sys_mc, n)))


def read_controller(sys_mc: str, mc: str) -> dict:
    mcdir = os.path.join(sys_mc, mc)
    name = (_read(os.path.join(mcdir, "mc_name")) or "").strip()
    ce = _read_int(os.path.join(mcdir, "ce_count")) or 0
    ue = _read_int(os.path.join(mcdir, "ue_count")) or 0
    size_mb = _read_int(os.path.join(mcdir, "size_mb"))
    dimms: list = []
    for n in sorted(os.listdir(mcdir)):
        if not n.startswith("dimm"):
            continue
        ddir = os.path.join(mcdir, n)
        if not os.path.isdir(ddir):
            continue
        dimms.append({
            "name": n,
            "label": (_read(os.path.join(ddir, "dimm_label"))
                       or "").strip() or None,
            "size_mb": _read_int(os.path.join(ddir, "size_mb")),
            "ce_count": _read_int(os.path.join(
                ddir, "dimm_ce_count")) or 0,
            "ue_count": _read_int(os.path.join(
                ddir, "dimm_ue_count")) or 0,
            "mem_type": (_read(os.path.join(ddir, "dimm_mem_type"))
                          or "").strip() or None,
            "dev_type": (_read(os.path.join(ddir, "dimm_dev_type"))
                          or "").strip() or None,
        })
    return {"name": mc, "driver": name or None,
            "ce_count": ce, "ue_count": ue,
            "size_mb": size_mb, "dimms": dimms}


_RECIPE_LOAD_DRIVER = (
    "# /sys/devices/system/edac/mc is empty — no EDAC controllers\n"
    "# detected. Either ECC is disabled in BIOS, the RAM is non-ECC,\n"
    "# or the per-platform EDAC kernel module is not loaded :\n"
    "#   AMD Ryzen / EPYC     →  sudo modprobe amd64_edac\n"
    "#   Intel Xeon / Core    →  sudo modprobe i7core_edac\n"
    "#                       or  sudo modprobe sb_edac (Sandy/Ivy)\n"
    "#                       or  sudo modprobe skx_edac (Skylake-X +)\n"
    "# Also check BIOS : ECC must be ENABLED (some boards default OFF\n"
    "# even with ECC DIMMs installed)."
)

_RECIPE_REPLACE_DIMM = (
    "# Uncorrectable error(s) detected — replace the flagged DIMM(s)\n"
    "# at the next maintenance window. Until then, treat all data on\n"
    "# this host as suspect (silent corruption may already have hit\n"
    "# the GGUF you mmapped). Locate the physical slot :\n"
    "for mc in /sys/devices/system/edac/mc/mc*; do\n"
    "  for d in $mc/dimm*; do\n"
    "    [ -d $d ] || continue\n"
    "    ue=$(cat $d/dimm_ue_count 2>/dev/null); ce=$(cat $d/dimm_ce_count 2>/dev/null)\n"
    "    if [ \"${ue:-0}\" -gt 0 ] || [ \"${ce:-0}\" -gt 0 ]; then\n"
    "      lbl=$(cat $d/dimm_label 2>/dev/null)\n"
    "      sz=$(cat $d/size_mb 2>/dev/null)\n"
    "      echo \"$d  label=$lbl  size=${sz}MB  ce=$ce  ue=$ue\"\n"
    "    fi\n"
    "  done\n"
    "done"
)

_RECIPE_MONITOR_CE = (
    "# Corrected errors are non-zero — DIMM is degrading but not\n"
    "# failed. Set up alerting before it gets worse :\n"
    "sudo apt install rasdaemon          # Debian/Ubuntu\n"
    "sudo systemctl enable --now rasdaemon\n"
    "# Or directly poll the counters in monitoring (cron / Prometheus\n"
    "# textfile collector) every few minutes."
)


def classify(controllers: list) -> dict:
    if not controllers:
        return {"verdict": "ecc_disabled",
                "reason": ("No EDAC memory controllers exposed at "
                           "/sys/devices/system/edac/mc — either "
                           "non-ECC RAM, ECC disabled in BIOS, or "
                           "the per-platform EDAC kernel module is "
                           "not loaded."),
                "recommendation": _RECIPE_LOAD_DRIVER}
    total_ue = sum(c.get("ue_count", 0) for c in controllers)
    total_ce = sum(c.get("ce_count", 0) for c in controllers)
    flagged: list = []
    for c in controllers:
        for d in c.get("dimms", []):
            if d.get("ue_count", 0) > 0 or d.get("ce_count", 0) > 0:
                flagged.append(
                    f"{c['name']}/{d['name']} "
                    f"(label={d.get('label') or '?'}) "
                    f"ce={d.get('ce_count', 0)} ue={d.get('ue_count', 0)}"
                )
    if total_ue > 0:
        return {"verdict": "ue_present",
                "reason": (f"Uncorrectable memory error(s) detected — "
                           f"total ue={total_ue} across "
                           f"{len(controllers)} controller(s). Data "
                           f"corruption may have already occurred. "
                           f"Flagged DIMM(s) : "
                           f"{', '.join(flagged) or 'unknown'}"),
                "recommendation": _RECIPE_REPLACE_DIMM}
    if total_ce > 0:
        return {"verdict": "ce_climbing",
                "reason": (f"Corrected (single-bit) errors total "
                           f"{total_ce} across {len(controllers)} "
                           f"controller(s). Cosmic-ray noise is fine, "
                           f"but a fast-climbing counter is an early "
                           f"DIMM-failure warning. Flagged : "
                           f"{', '.join(flagged) or 'see DIMM list'}"),
                "recommendation": _RECIPE_MONITOR_CE}
    return {"verdict": "ecc_clean",
            "reason": (f"ECC enabled on {len(controllers)} "
                       f"controller(s), zero errors recorded "
                       f"since boot."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir(_SYS_EDAC_MC):
        return {
            "ok": False,
            "verdict": {"verdict": "unknown",
                         "reason": ("/sys/devices/system/edac "
                                    "unreadable."),
                         "recommendation": ""},
            "controllers": [], "ce_total": 0, "ue_total": 0,
        }
    mcs = list_controllers(_SYS_EDAC_MC)
    controllers = [read_controller(_SYS_EDAC_MC, m) for m in mcs]
    verdict = classify(controllers)
    return {
        "ok": True,
        "controller_count": len(controllers),
        "controllers": controllers,
        "ce_total": sum(c.get("ce_count", 0) for c in controllers),
        "ue_total": sum(c.get("ue_count", 0) for c in controllers),
        "verdict": verdict,
    }
