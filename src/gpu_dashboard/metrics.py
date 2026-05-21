"""Background sampler that polls GPU metrics and stores them in a rolling buffer.

The sampler runs in a daemon thread and is woken up every `interval` seconds.
Each sample is a dict of stats from `nvidia-smi` + (optionally) `nvidia-settings`
for per-fan RPMs.
"""
from __future__ import annotations

import collections
import datetime
import re
import subprocess
import threading
import time as _time
from typing import List, Optional


_NVIDIA_SMI_QUERY = (
    "temperature.gpu,fan.speed,clocks.current.graphics,"
    "clocks.current.memory,power.draw"
)


class MetricsSampler:
    """Thread-safe rolling buffer of GPU samples.

    Usage:
        sampler = MetricsSampler(interval=5, maxlen=720)
        sampler.start()
        # later:
        samples = sampler.snapshot()
    """

    def __init__(
        self,
        interval: float = 5.0,
        maxlen: int = 720,
        nvidia_settings_display: Optional[str] = None,
        nvidia_settings_xauthority: Optional[str] = None,
    ):
        self.interval = interval
        self._buffer = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._display = nvidia_settings_display
        self._xauth = nvidia_settings_xauthority

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="gpu-metrics-sampler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def snapshot(self) -> List[dict]:
        with self._lock:
            return list(self._buffer)

    def _loop(self) -> None:
        while not self._stop.is_set():
            sample = self._poll()
            if sample is not None:
                with self._lock:
                    self._buffer.append(sample)
            # Sleep responsive to stop()
            self._stop.wait(self.interval)

    def _poll(self) -> Optional[dict]:
        try:
            out = subprocess.run(
                ["nvidia-smi",
                 f"--query-gpu={_NVIDIA_SMI_QUERY}",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            return None
        if out.returncode != 0:
            return None

        parts = [p.strip() for p in out.stdout.strip().split(",")] if out.stdout.strip() else []
        if len(parts) < 5 or not parts[0].isdigit():
            return None

        sample = {
            "ts": datetime.datetime.now().strftime("%H:%M:%S"),
            "temp": int(parts[0]),
            "fan": int(parts[1]) if parts[1].isdigit() else 0,
            "clk_gpu": int(parts[2]) if parts[2].isdigit() else 0,
            "clk_mem": int(parts[3]) if parts[3].isdigit() else 0,
            "power": float(parts[4]),
        }

        # Per-fan RPM via nvidia-settings, if a DISPLAY is configured
        if self._display:
            f0, f1 = self._query_per_fan_rpm()
            sample["fan0_rpm"] = f0
            sample["fan1_rpm"] = f1

        return sample

    def _query_per_fan_rpm(self) -> tuple:
        """Query nvidia-settings for per-fan RPM. Returns (fan0, fan1) ints (0 if unknown)."""
        import os
        env = os.environ.copy()
        env["DISPLAY"] = self._display
        if self._xauth:
            env["XAUTHORITY"] = self._xauth
        try:
            r = subprocess.run(
                ["nvidia-settings",
                 "-q", "[fan:0]/GPUCurrentFanSpeedRPM",
                 "-q", "[fan:1]/GPUCurrentFanSpeedRPM"],
                capture_output=True, text=True, timeout=3, env=env,
            )
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            return (0, 0)
        if r.returncode != 0:
            return (0, 0)

        rpms = [0, 0]
        for m in re.finditer(r"\[fan:(\d+)\]\): (\d+)", r.stdout):
            idx = int(m.group(1))
            if idx < 2:
                rpms[idx] = int(m.group(2))
        return tuple(rpms)
