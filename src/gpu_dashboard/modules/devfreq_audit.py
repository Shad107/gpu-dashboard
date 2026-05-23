"""Module devfreq_audit — /sys/class/devfreq (R&D #62.1).

The Linux devfreq subsystem provides cpufreq-style DVFS scaling
for non-CPU devices : GPU memory controllers, DDR DVFS, NPUs,
on-die accelerators. On x86 desktop these are rare ; on ARM
SoCs they're everywhere.

Why this matters on an LLM rig (esp. with on-die NPU / iGPU) :

* A device parked at min_freq via the `powersave` governor while
  inference runs cuts memory bandwidth in half — invisible to
  cpufreq tools, never reported by `nvidia-smi`.
* Stuck-at-max wastes idle power.
* `userspace` governor with no daemon to drive it is a frozen
  half-config.

Reads :
  /sys/class/devfreq/<dev>/{governor, cur_freq, min_freq,
                                max_freq, available_governors,
                                target_freq, trans_stat}

Verdicts (priority-ordered) :
  stuck_min                ≥1 device with cur_freq = min_freq
                           AND min < max (i.e., parked).
  stuck_max                ≥1 device with cur_freq = max_freq
                           AND under a non-`performance` governor.
  userspace_governor       ≥1 device on `userspace` governor with
                           no apparent userspace driver.
  pinned_perf              ≥1 device on `performance` governor
                           (informational — wastes idle power).
  ok                       all devices on a healthy governor.
  unknown                  /sys/class/devfreq absent / empty.

stdlib only.
"""
from __future__ import annotations

import os
from typing import List, Optional


NAME = "devfreq_audit"


_SYS_DEVFREQ = "/sys/class/devfreq"


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


def list_devices(sys_devfreq: str = _SYS_DEVFREQ) -> List[dict]:
    if not os.path.isdir(sys_devfreq):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_devfreq)):
        d = os.path.join(sys_devfreq, name)
        if not os.path.isdir(d):
            continue
        out.append({
            "name": name,
            "governor": _read(os.path.join(d, "governor")),
            "cur_freq": _read_int(os.path.join(d, "cur_freq")),
            "min_freq": _read_int(os.path.join(d, "min_freq")),
            "max_freq": _read_int(os.path.join(d, "max_freq")),
            "available_governors": (
                (_read(os.path.join(d, "available_governors"))
                 or "").split()),
            "target_freq": _read_int(
                os.path.join(d, "target_freq")),
        })
    return out


def classify(devices: List[dict]) -> dict:
    if not devices:
        return {"verdict": "unknown",
                "reason": ("/sys/class/devfreq absent or empty — "
                          "no DVFS scaling devices."),
                "recommendation": ""}

    # 1) stuck_min — cur == min AND min < max
    stuck_min = []
    for d in devices:
        cur, mn, mx = d.get("cur_freq"), d.get("min_freq"), d.get(
            "max_freq")
        if (cur is not None and mn is not None and mx is not None
                and mn < mx and cur == mn):
            stuck_min.append(
                f"{d['name']}({cur}/{mx})")
    if stuck_min:
        return {"verdict": "stuck_min",
                "reason": (f"{len(stuck_min)} device(s) parked at "
                          f"min_freq : {stuck_min[0]}. Memory / NPU "
                          f"bandwidth halved silently."),
                "recommendation": _recipe_perf()}

    # 2) stuck_max — cur == max AND governor != performance/powersave
    stuck_max = []
    for d in devices:
        cur, mx = d.get("cur_freq"), d.get("max_freq")
        gov = (d.get("governor") or "").lower()
        if (cur is not None and mx is not None and cur == mx and
                gov not in ("performance", "powersave",
                              "simple_ondemand")):
            stuck_max.append(
                f"{d['name']}({mx} on {gov})")
    if stuck_max:
        return {"verdict": "stuck_max",
                "reason": (f"{len(stuck_max)} device(s) pinned at "
                          f"max_freq with non-perf governor : "
                          f"{stuck_max[0]}. Idle power wasted."),
                "recommendation": _recipe_perf()}

    # 3) userspace_governor
    user = [d["name"] for d in devices
              if (d.get("governor") or "").lower() == "userspace"]
    if user:
        return {"verdict": "userspace_governor",
                "reason": (f"{len(user)} device(s) on 'userspace' "
                          f"governor : {', '.join(user[:3])}. "
                          f"Frozen unless a daemon drives them."),
                "recommendation": _recipe_simple_ondemand()}

    # 4) pinned_perf (informational accent)
    perf = [d["name"] for d in devices
              if (d.get("governor") or "").lower() == "performance"]
    if perf:
        return {"verdict": "pinned_perf",
                "reason": (f"{len(perf)} device(s) on 'performance' "
                          f"governor : {', '.join(perf[:3])}. "
                          f"Power wasted at idle."),
                "recommendation": _recipe_simple_ondemand()}

    return {"verdict": "ok",
            "reason": (f"{len(devices)} devfreq device(s) on "
                      f"healthy governors."),
            "recommendation": ""}


def status(config=None, sys_devfreq: str = _SYS_DEVFREQ) -> dict:
    devices = list_devices(sys_devfreq)
    ok = bool(devices)
    verdict = classify(devices)
    return {"ok": ok,
              "device_count": len(devices),
              "devices": devices,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_perf() -> str:
    return ("# Switch the affected device(s) to a responsive\n"
            "# governor :\n"
            "for d in /sys/class/devfreq/*; do\n"
            "  echo simple_ondemand | sudo tee $d/governor 2>/dev/null \\\n"
            "    || echo performance | sudo tee $d/governor\n"
            "done\n"
            "# Persist via udev rule or a small systemd unit.\n")


def _recipe_simple_ondemand() -> str:
    return ("# Move off the userspace / performance governor :\n"
            "for d in /sys/class/devfreq/*; do\n"
            "  cur=$(cat $d/governor)\n"
            "  case \"$cur\" in userspace|performance)\n"
            "    echo simple_ondemand | sudo tee $d/governor ;;\n"
            "  esac\n"
            "done\n")
