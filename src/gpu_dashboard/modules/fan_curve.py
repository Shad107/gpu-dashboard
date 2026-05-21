"""Module fan_curve — custom fan curve daemon for NVIDIA cards.

Replaces the stock NVIDIA fan curve with a user-defined one. Polls GPU temp
every `interval` seconds and applies the target fan % via nvidia-settings.

Prereqs (same as clock_offsets):
- Coolbits ≥ 4 in xorg.conf (bit 2 = manual fan control sliders)
- An X server attached to the NVIDIA (DISPLAY + XAUTHORITY)

Public API:
- interpolate(curve, temp_c) → fan_pct (int)  — piecewise linear
- validate_curve(curve) → raises ValueError on bad shape
- apply_fan_speed(target_pct, display, xauthority) → dict
- pick_curve(profile, override=None) → list of [temp, fan_pct]
- FanCurveDaemon class : start/stop daemon thread
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time as _time
from typing import List, Optional


NAME = "fan_curve"

# Conservative default curve (works for any modern NVIDIA card)
_DEFAULT_CURVE = [[30, 0], [50, 30], [65, 50], [75, 70], [85, 100]]


# ──────────────────────────── pure functions ──────────────────────────────


def interpolate(curve, temp_c: float) -> int:
    """Piecewise linear interp : temp °C → fan % (0-100).

    - Below first point : returns first point's fan %
    - Above last point  : returns last point's fan %
    - On a point        : returns exact value
    - Empty curve       : returns 0
    """
    pts = list(curve) if curve else []
    if not pts:
        return 0
    pts.sort(key=lambda p: p[0])
    if len(pts) == 1:
        return int(pts[0][1])
    if temp_c <= pts[0][0]:
        return int(pts[0][1])
    if temp_c >= pts[-1][0]:
        return int(pts[-1][1])
    for i in range(len(pts) - 1):
        t0, f0 = pts[i]
        t1, f1 = pts[i + 1]
        if t0 <= temp_c <= t1:
            if t1 == t0:
                return int(f0)
            ratio = (temp_c - t0) / (t1 - t0)
            return int(round(f0 + ratio * (f1 - f0)))
    return int(pts[-1][1])


def validate_curve(curve) -> None:
    """Raise ValueError if curve isn't a valid list of [temp, fan_pct] pairs."""
    if not curve:
        raise ValueError("fan curve is empty")
    last_t = None
    for i, pt in enumerate(curve):
        if not isinstance(pt, (list, tuple)) or len(pt) != 2:
            raise ValueError(f"point {i} must be [temp, fan_pct]")
        t, p = pt
        if not isinstance(t, (int, float)) or not (0 <= t <= 110):
            raise ValueError(f"point {i}: temp {t} out of range [0, 110]")
        if not isinstance(p, (int, float)) or not (0 <= p <= 100):
            raise ValueError(f"point {i}: fan_pct {p} out of range [0, 100]")
        if last_t is not None and t <= last_t:
            raise ValueError(f"point {i}: temp not monotonic increasing")
        last_t = t


def pick_curve(profile: Optional[dict] = None, override: Optional[list] = None) -> list:
    """Return the active curve.

    Priority order :
      1. explicit `override` argument (used by the daemon for live edits)
      2. user-saved override file ~/.config/gpu-dashboard/fan_curve.json
      3. profile.fans.default_curve (per-GPU profile JSON)
      4. built-in safe default
    """
    if override is not None:
        return override
    # User override file (saved via POST /api/fan-curve)
    import json as _json
    import os as _os
    override_path = _os.path.expanduser("~/.config/gpu-dashboard/fan_curve.json")
    if _os.path.exists(override_path):
        try:
            with open(override_path) as f:
                data = _json.load(f)
            c = data.get("curve") if isinstance(data, dict) else None
            if c and isinstance(c, list) and len(c) >= 2:
                return c
        except (OSError, _json.JSONDecodeError):
            pass  # corrupted file → fall through to profile/default
    if profile:
        c = (profile.get("fans") or {}).get("default_curve")
        if c:
            return c
    return list(_DEFAULT_CURVE)


def validate_curve(curve) -> tuple:
    """Validate a user-supplied curve. Returns (ok, error_msg).

    Rules :
      - List of [int, int] pairs
      - At least 2 points
      - All temps and fans in [0, 100]
      - Sorted strictly ascending by temp (no duplicates)
    """
    if not isinstance(curve, list):
        return False, "curve must be a list"
    if len(curve) < 2:
        return False, "curve must have at least 2 control points"
    prev_t = -1
    for i, p in enumerate(curve):
        if not (isinstance(p, list) and len(p) == 2):
            return False, f"point {i}: must be [temp, fan]"
        t, f = p
        if not (isinstance(t, int) and isinstance(f, int)):
            return False, f"point {i}: temp and fan must be integers"
        if not (0 <= t <= 100):
            return False, f"point {i}: temp {t} out of range [0,100]"
        if not (0 <= f <= 100):
            return False, f"point {i}: fan {f} out of range [0,100]"
        if t <= prev_t:
            return False, f"point {i}: curve must be sorted by temp (got {t} after {prev_t})"
        prev_t = t
    return True, ""


# ──────────────────────────── apply via nvidia-settings ────────────────────


def apply_fan_speed(
    target_pct: int,
    display: str = ":0",
    xauthority: Optional[str] = None,
    fan_indexes: Optional[list] = None,
) -> dict:
    """Apply `target_pct` to all fans of GPU 0 via nvidia-settings.

    Sets GPUFanControlState=1 (manual) + GPUTargetFanSpeed=N on each fan.
    Returns {ok, pct, output, error?}.
    """
    if not isinstance(target_pct, (int, float)) or not (0 <= target_pct <= 100):
        raise ValueError(f"target_pct {target_pct} out of range [0, 100]")
    pct = int(target_pct)
    env = os.environ.copy()
    env["DISPLAY"] = display
    if xauthority:
        env["XAUTHORITY"] = xauthority

    # First enable manual control for GPU 0
    args = ["nvidia-settings", "-a", "[gpu:0]/GPUFanControlState=1"]
    # Then apply to each known fan (default 0-3, the daemon discovers actuals)
    indexes = fan_indexes if fan_indexes is not None else [0, 1, 2, 3]
    for i in indexes:
        args += ["-a", f"[fan:{i}]/GPUTargetFanSpeed={pct}"]

    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=4, env=env)
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
        return {"ok": False, "pct": pct, "output": "", "error": str(e)}

    return {
        "ok": r.returncode == 0,
        "pct": pct,
        "output": r.stdout.strip(),
        "error": r.stderr.strip() or None,
    }


# ──────────────────────────── daemon thread ────────────────────────────────


class FanCurveDaemon:
    """Background thread that reads GPU temp and applies the fan curve."""

    def __init__(
        self,
        curve: list,
        display: str = ":0",
        xauthority: Optional[str] = None,
        interval: float = 5.0,
        sampler=None,
    ):
        self._curve = curve
        self._display = display
        self._xauth = xauthority
        self._interval = interval
        self._sampler = sampler  # optional: read temp from latest sample instead of nvidia-smi
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_pct: Optional[int] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="fan-curve")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def update_curve(self, new_curve: list) -> None:
        validate_curve(new_curve)
        self._curve = new_curve

    def _read_temp(self) -> Optional[int]:
        # Prefer the latest sample from the running sampler (no extra subprocess)
        if self._sampler is not None:
            buf = self._sampler.snapshot()
            if buf:
                return int(buf[-1].get("temp") or 0)
        # Fallback : direct nvidia-smi
        try:
            r = subprocess.run(
                ["nvidia-smi", "-i", "0",
                 "--query-gpu=temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3,
            )
            if r.returncode == 0 and r.stdout.strip().isdigit():
                return int(r.stdout.strip())
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            pass
        return None

    def _loop(self) -> None:
        while not self._stop.is_set():
            temp = self._read_temp()
            if temp is not None:
                pct = interpolate(self._curve, temp)
                # Avoid spamming nvidia-settings if value hasn't changed
                if pct != self._last_pct:
                    apply_fan_speed(pct, self._display, self._xauth)
                    self._last_pct = pct
            self._stop.wait(self._interval)
