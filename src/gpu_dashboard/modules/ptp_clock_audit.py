"""Module ptp_clock_audit — PTP hardware clocks (R&D #63.2).

Reads /sys/class/ptp/ptp*/{clock_name, max_adjustment, n_alarm,
n_ext_ts, n_per_out, n_pins, pps_available} + /dev/ptp* perms.

Distinct from R&D #49.x rtc_clock_audit (battery RTC + /sys/class/
pps for PPS source) and clock_offsets / clocksource_audit. The PTP
subsystem owns the IEEE-1588 hardware clocks on NICs (and some
on-package PTP) that chrony/ptp4l can use as a refclock.

Why this matters on a cluster / homelab LLM rig :

* The NIC exposes a PHC (PTP Hardware Clock) but no chrony/ptp4l
  daemon binds it — system clock drifts vs cluster peers despite
  'having PTP HW'.
* max_adjustment near zero → kernel can't tune the PHC ; the
  clock is useless as a refclock for higher-level daemons.
* /dev/ptp* exists but mode 0600 root → most monitoring tools
  can't open it.

Reads :
  /sys/class/ptp/ptp*/{clock_name, max_adjustment, n_alarm,
                          n_ext_ts, n_per_out, n_pins,
                          pps_available}
  /dev/ptp*  perms via os.stat

Verdicts (priority-ordered) :
  max_adjustment_zero       ≥1 PHC with max_adjustment = 0 → PHC
                            cannot be steered, useless as ref.
  phc_unused                ≥1 PHC present but /dev/ptp* has
                            mode 0600 (no daemon can read).
  sw_timestamping_only      PHC count == 0 (informational on a
                            cluster host).
  ok                        PHCs present and usable.
  unknown                   /sys/class/ptp absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
import stat
from typing import List, Optional


NAME = "ptp_clock_audit"


_SYS_PTP = "/sys/class/ptp"
_DEV = "/dev"

_PTP_DIR_RE = re.compile(r"^ptp\d+$")


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


def list_phcs(sys_ptp: str = _SYS_PTP) -> List[dict]:
    if not os.path.isdir(sys_ptp):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_ptp)):
        if not _PTP_DIR_RE.match(name):
            continue
        d = os.path.join(sys_ptp, name)
        out.append({
            "id": name,
            "clock_name": _read(os.path.join(d, "clock_name")),
            "max_adjustment": _read_int(
                os.path.join(d, "max_adjustment")),
            "n_alarm": _read_int(os.path.join(d, "n_alarm")),
            "n_ext_ts": _read_int(os.path.join(d, "n_ext_ts")),
            "n_per_out": _read_int(
                os.path.join(d, "n_per_out")),
            "n_pins": _read_int(os.path.join(d, "n_pins")),
            "pps_available": _read_int(
                os.path.join(d, "pps_available")),
        })
    return out


def list_dev_perms(dev: str = _DEV) -> List[dict]:
    if not os.path.isdir(dev):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(dev)):
        if not name.startswith("ptp"):
            continue
        p = os.path.join(dev, name)
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({
            "name": name,
            "mode": stat.S_IMODE(st.st_mode),
            "uid": st.st_uid, "gid": st.st_gid,
        })
    return out


def classify(phcs: List[dict], dev_perms: List[dict],
              sys_ptp_present: bool) -> dict:
    if not sys_ptp_present:
        return {"verdict": "unknown",
                "reason": ("/sys/class/ptp not present — kernel "
                          "built without PTP or no NIC exposes "
                          "a PHC."),
                "recommendation": ""}

    if not phcs:
        return {"verdict": "sw_timestamping_only",
                "reason": ("/sys/class/ptp directory present but "
                          "no PHC enumerated — only SW timestamping "
                          "available."),
                "recommendation": ""}

    # 1) max_adjustment_zero
    zero = [p for p in phcs
              if p.get("max_adjustment") is not None and
                 p["max_adjustment"] == 0]
    if zero:
        sample = ", ".join(
            f"{p['id']}({p.get('clock_name')})"
            for p in zero[:3])
        return {"verdict": "max_adjustment_zero",
                "reason": (f"{len(zero)} PHC(s) with "
                          f"max_adjustment = 0 : {sample}. "
                          f"Cannot be steered, useless as refclock."),
                "recommendation": _recipe_max_adj()}

    # 2) phc_unused — /dev/ptp* present but mode 0600 (root-only)
    bad_perm = [d for d in dev_perms
                   if d["mode"] == 0o600]
    if bad_perm and phcs:
        sample = ", ".join(
            f"{d['name']}(0o{d['mode']:o})" for d in bad_perm[:3])
        return {"verdict": "phc_unused",
                "reason": (f"{len(bad_perm)} PHC device node(s) "
                          f"root-only : {sample}. Monitoring "
                          f"tools can't open them — likely no "
                          f"chrony / ptp4l daemon configured."),
                "recommendation": _recipe_phc_unused()}

    return {"verdict": "ok",
            "reason": (f"{len(phcs)} PHC(s) present, "
                      f"steerable, monitoring-ready."),
            "recommendation": ""}


def status(config=None,
            sys_ptp: str = _SYS_PTP,
            dev: str = _DEV) -> dict:
    sys_ptp_present = os.path.isdir(sys_ptp)
    phcs = list_phcs(sys_ptp)
    dev_perms = list_dev_perms(dev)
    ok = sys_ptp_present
    verdict = classify(phcs, dev_perms, sys_ptp_present)
    return {"ok": ok,
              "sys_ptp_present": sys_ptp_present,
              "phc_count": len(phcs),
              "phcs": phcs,
              "dev_perms": dev_perms,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_max_adj() -> str:
    return ("# Inspect why the kernel can't steer this PHC :\n"
            "for p in /sys/class/ptp/ptp*; do\n"
            "  echo \"$(cat $p/clock_name) max_adj=$(cat $p/max_adjustment)\"\n"
            "done\n"
            "# Vendor NIC firmware update often restores adjustability.\n")


def _recipe_phc_unused() -> str:
    return ("# Configure chrony to use the PHC as refclock :\n"
            "# /etc/chrony/chrony.conf :\n"
            "#   refclock PHC /dev/ptp0 poll 0 dpoll -2 offset 0\n"
            "# Or run ptp4l + phc2sys :\n"
            "sudo apt install linuxptp\n"
            "sudo systemctl enable --now ptp4l@<iface>\n"
            "sudo systemctl enable --now phc2sys@<iface>\n")
