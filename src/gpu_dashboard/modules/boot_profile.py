"""Module boot_profile — apply a stored GPU profile shortly after boot (R&D #15.8).

LLM rigs running 24/7 lose their power-limit / clock-offset / fan-curve
configuration on every reboot (those are not persisted in BIOS). The
typical workaround is a one-off shell script in cron. This module
replaces that with :

  1. A profile JSON saved by the dashboard at apply-time :
       ~/.config/gpu-dashboard/boot_profile.json
       {
         "name": "silent-night",
         "power_limit_w": 250,
         "gpu_clock_offset_mhz": -50,
         "mem_clock_offset_mhz": 500,
         "fan_curve": [[40, 30], [70, 80], [85, 100]],
         "persistence_mode": true
       }
  2. A systemd user unit `gpu-dashboard-boot-profile.service` that runs
     `python3 -m gpu_dashboard.modules.boot_profile apply` after boot.
  3. A retry loop : poll `nvidia-smi -L` every 1s for up to 30s before
     attempting the apply (driver may not be fully ready immediately).
  4. Records latency + outcome in
     ~/.config/gpu-dashboard/boot_profile_history.json.

Module-only API ; HTTP integration is in api/boot_profile.py.

stdlib only.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Optional


NAME = "boot_profile"

_PROFILE_PATH = "~/.config/gpu-dashboard/boot_profile.json"
_HISTORY_PATH = "~/.config/gpu-dashboard/boot_profile_history.json"
_HISTORY_MAX = 50

# Boot-readiness probe : how long to wait for nvidia-smi to come up
_READY_TIMEOUT_S = 30
_READY_POLL_S = 1.0


def profile_path() -> str:
    return os.path.expanduser(_PROFILE_PATH)


def history_path() -> str:
    return os.path.expanduser(_HISTORY_PATH)


def load_profile() -> Optional[dict]:
    p = profile_path()
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_profile(profile: dict) -> None:
    p = profile_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(profile, f, indent=2)


def clear_profile() -> bool:
    p = profile_path()
    if os.path.exists(p):
        try:
            os.remove(p)
            return True
        except OSError:
            return False
    return False


def load_history() -> list:
    p = history_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p) as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def append_history(entry: dict) -> None:
    log = load_history()
    log.append(entry)
    log = log[-_HISTORY_MAX:]
    p = history_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(log, f, indent=2)


def wait_for_driver(timeout_s: float = _READY_TIMEOUT_S,
                    poll_s: float = _READY_POLL_S) -> dict:
    """Poll `nvidia-smi -L` until it succeeds or timeout. Returns
    {ready, attempts, elapsed_s, error?}."""
    deadline = time.monotonic() + timeout_s
    attempts = 0
    last_err = None
    while time.monotonic() < deadline:
        attempts += 1
        try:
            r = subprocess.run(["nvidia-smi", "-L"],
                                capture_output=True, text=True, timeout=2)
        except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
            last_err = str(e)
        else:
            if r.returncode == 0 and "GPU " in r.stdout:
                return {"ready": True, "attempts": attempts,
                        "elapsed_s": round(time.monotonic() - (deadline - timeout_s), 2)}
            last_err = r.stderr.strip() or "non-zero exit"
        time.sleep(poll_s)
    return {"ready": False, "attempts": attempts,
            "elapsed_s": round(timeout_s, 2), "error": last_err}


def apply_profile(profile: dict, gpu_index: int = 0,
                  ready: Optional[dict] = None) -> dict:
    """Apply the persisted profile to the GPU. Returns an outcome dict
    suitable for the history log."""
    started = time.time()
    if ready is None:
        ready = wait_for_driver()
    if not ready.get("ready"):
        outcome = {
            "ok": False, "ts": int(started),
            "reason": "driver did not initialise within timeout",
            "ready_probe": ready, "applied": {},
        }
        append_history(outcome)
        return outcome

    applied: dict = {}
    errors: list = []
    cfg_pl = profile.get("power_limit_w")
    cfg_g_off = profile.get("gpu_clock_offset_mhz")
    cfg_m_off = profile.get("mem_clock_offset_mhz")
    cfg_pm = profile.get("persistence_mode")
    cfg_fan = profile.get("fan_curve")

    if cfg_pl is not None:
        cmd = ["nvidia-smi", "-i", str(gpu_index), "-pl", str(int(cfg_pl))]
        ok, msg = _run(cmd)
        applied["power_limit_w"] = {"ok": ok, "value": cfg_pl, "msg": msg[:120]}
        if not ok:
            errors.append(f"-pl {cfg_pl}: {msg[:60]}")

    if cfg_pm is not None:
        cmd = ["nvidia-smi", "-i", str(gpu_index), "-pm", "1" if cfg_pm else "0"]
        ok, msg = _run(cmd)
        applied["persistence_mode"] = {"ok": ok, "value": cfg_pm, "msg": msg[:120]}
        if not ok:
            errors.append(f"-pm {cfg_pm}: {msg[:60]}")

    # Clock offsets and fan curve : leave to the existing dashboard module
    # (they're more involved). Note them as advisory in the outcome.
    if cfg_g_off is not None:
        applied["gpu_clock_offset_mhz"] = {"deferred": True, "value": cfg_g_off,
                                            "note": "apply via clock_offsets module"}
    if cfg_m_off is not None:
        applied["mem_clock_offset_mhz"] = {"deferred": True, "value": cfg_m_off}
    if cfg_fan is not None:
        applied["fan_curve"] = {"deferred": True, "points_count": len(cfg_fan)}

    outcome = {
        "ok": not errors,
        "ts": int(started),
        "ready_probe": ready,
        "applied": applied,
        "errors": errors,
        "profile_name": profile.get("name", "?"),
    }
    append_history(outcome)
    return outcome


def _run(cmd: list, timeout: float = 5.0) -> tuple:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
        return False, f"exec failed: {e}"
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()
    return True, (r.stdout or "ok").strip()


def status() -> dict:
    """Read profile + latest history entry for the UI."""
    prof = load_profile()
    hist = load_history()
    return {
        "ok": True,
        "configured": prof is not None,
        "profile": prof,
        "last_outcome": hist[-1] if hist else None,
        "history_count": len(hist),
    }


# Convenience CLI entrypoint : `python -m gpu_dashboard.modules.boot_profile apply`
def main(argv: Optional[list] = None) -> int:
    import sys
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] != "apply":
        sys.stderr.write("Usage: boot_profile apply\n")
        return 2
    prof = load_profile()
    if not prof:
        sys.stderr.write("No boot profile configured.\n")
        return 0
    outcome = apply_profile(prof)
    print(json.dumps(outcome, indent=2))
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
