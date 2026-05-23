"""Module rtc_clock_audit — RTC + PPS + hrtimer (R&D #49.4).

Reads /sys/class/rtc/* (RTC hardware clock state, since_epoch +
wakealarm + hctosys) and /sys/class/pps/* (PPS-discipline source).
/proc/timer_list is usually root-only ; skipped here.

The actionable signals :

  RTC drift     |rtc_since_epoch - system_time| > 60s → the
                hardware clock is significantly out of sync with
                the system clock. After resume, this is the
                clock that's used to seed system time.
  no_pps_source PPS framework loaded but no source → systems
                that need sub-second precision (NTP server,
                hardware-timestamped trading) lose accuracy.
  wakealarm_set The wakealarm file is non-empty → a future wake
                is scheduled (rtcwake / suspend-then-resume).

Verdicts (priority-ordered) :
  rtc_drift_high    |rtc - system_time| ≥ 60 seconds.
  hctosys_disabled  /sys/class/rtc/rtc0/hctosys = 0 → boot will
                    not pull system clock from RTC.
  no_rtc            /sys/class/rtc empty.
  ok                drift < 60s + hctosys enabled.
  unknown           /sys/class/rtc unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import time
from typing import Optional


NAME = "rtc_clock_audit"


_SYS_CLASS_RTC = "/sys/class/rtc"
_SYS_CLASS_PPS = "/sys/class/pps"


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


def list_rtcs(sys_rtc: str = _SYS_CLASS_RTC) -> list:
    if not os.path.isdir(sys_rtc):
        return []
    out: list = []
    try:
        for name in sorted(os.listdir(sys_rtc)):
            if not name.startswith("rtc"):
                continue
            d = os.path.join(sys_rtc, name)
            out.append({
                "name": name,
                "rtc_name": (_read(os.path.join(d, "name"))
                                or "").strip() or None,
                "since_epoch": _read_int(os.path.join(d, "since_epoch")),
                "date": (_read(os.path.join(d, "date"))
                            or "").strip() or None,
                "time": (_read(os.path.join(d, "time"))
                            or "").strip() or None,
                "hctosys": _read_int(os.path.join(d, "hctosys")),
                "wakealarm": (_read(os.path.join(d, "wakealarm"))
                                  or "").strip() or None,
                "max_user_freq": _read_int(
                    os.path.join(d, "max_user_freq")),
            })
    except OSError:
        return []
    return out


def list_pps(sys_pps: str = _SYS_CLASS_PPS) -> list:
    if not os.path.isdir(sys_pps):
        return []
    try:
        return sorted(os.listdir(sys_pps))
    except OSError:
        return []


_DRIFT_THRESHOLD_SEC = 60


_RECIPE_DRIFT_HIGH = (
    "# Hardware RTC has drifted > 60 s from system clock. Sync :\n"
    "sudo hwclock --systohc          # push system → RTC\n"
    "# Or pull RTC → system :\n"
    "sudo hwclock --hctosys\n"
    "# If drift recurs : check CMOS battery (CR2032 on motherboard\n"
    "# for desktops, ML1220 for some laptops). A dying battery\n"
    "# typically drifts seconds-per-day."
)

_RECIPE_HCTOSYS = (
    "# hctosys=0 — at boot the kernel will NOT pull system time\n"
    "# from the RTC. Usually fine when chronyd / systemd-timesyncd\n"
    "# pulls NTP early enough, but problematic on offline rigs.\n"
    "# Re-enable :\n"
    "echo Y | sudo tee /sys/module/rtc_cmos/parameters/hctosys"
)


def classify(rtcs: list, pps_sources: list,
              now_epoch: Optional[float] = None) -> dict:
    if not rtcs:
        return {"verdict": "no_rtc",
                "reason": ("/sys/class/rtc empty — no hardware "
                           "RTC exposed."),
                "recommendation": ""}
    if now_epoch is None:
        now_epoch = time.time()
    head = rtcs[0]
    se = head.get("since_epoch")
    if isinstance(se, int):
        drift = abs(se - int(now_epoch))
        if drift >= _DRIFT_THRESHOLD_SEC:
            return {"verdict": "rtc_drift_high",
                    "reason": (f"RTC since_epoch={se} vs system "
                               f"clock={int(now_epoch)} — drift "
                               f"{drift}s ≥ {_DRIFT_THRESHOLD_SEC}s."),
                    "recommendation": _RECIPE_DRIFT_HIGH}
    if head.get("hctosys") == 0:
        return {"verdict": "hctosys_disabled",
                "reason": ("RTC hctosys=0 — boot will not pull "
                           "system time from RTC. Fine if NTP "
                           "starts before any time-sensitive "
                           "service ; problematic on offline rigs."),
                "recommendation": _RECIPE_HCTOSYS}
    return {"verdict": "ok",
            "reason": (f"{len(rtcs)} RTC ({head.get('rtc_name')}), "
                       f"drift < {_DRIFT_THRESHOLD_SEC}s, "
                       f"hctosys={head.get('hctosys')}, "
                       f"{len(pps_sources)} PPS source(s)."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir(_SYS_CLASS_RTC):
        return {
            "ok": False,
            "verdict": {"verdict": "unknown",
                         "reason": "/sys/class/rtc unreadable.",
                         "recommendation": ""},
            "rtcs": [], "pps_sources": [],
        }
    rtcs = list_rtcs(_SYS_CLASS_RTC)
    pps_sources = list_pps(_SYS_CLASS_PPS)
    verdict = classify(rtcs, pps_sources)
    return {
        "ok": True,
        "rtc_count": len(rtcs),
        "rtcs": rtcs,
        "pps_sources": pps_sources,
        "system_epoch": int(time.time()),
        "verdict": verdict,
    }
