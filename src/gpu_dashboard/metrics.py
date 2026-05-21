"""Background sampler that polls GPU metrics, keeps a rolling buffer + optionally
persists each sample in a Storage (SQLite) instance.

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
    "temperature.gpu,fan.speed,clocks.current.graphics,clocks.current.memory,"
    "power.draw,power.limit,utilization.gpu,memory.used"
)


class MetricsSampler:
    """Thread-safe rolling buffer of GPU samples, with optional SQLite persistence.

    Usage:
        from gpu_dashboard.storage import Storage
        storage = Storage("~/.local/share/gpu-dashboard/metrics.db")
        sampler = MetricsSampler(interval=5, maxlen=720, storage=storage)
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
        storage=None,
    ):
        self.interval = interval
        self._buffer = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._display = nvidia_settings_display
        self._xauth = nvidia_settings_xauthority
        self._storage = storage

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

    def record_event(self, kind: str, payload: Optional[dict] = None) -> None:
        """Forward to storage if available, no-op otherwise."""
        if self._storage is not None:
            try:
                self._storage.record_event(kind, payload)
            except Exception:
                pass  # never let a logging failure break the caller

    def _loop(self) -> None:
        while not self._stop.is_set():
            sample = self._poll()
            if sample is not None:
                with self._lock:
                    self._buffer.append(sample)
                self._persist(sample)
            self._stop.wait(self.interval)

    def _persist(self, sample: dict) -> None:
        """Write the sample to storage with epoch ts + DB column mapping."""
        if self._storage is None:
            return
        try:
            db_sample = {
                "ts": int(_time.time()),
                "temp": sample.get("temp"),
                "fan_pct": sample.get("fan"),
                "fan0_rpm": sample.get("fan0_rpm"),
                "fan1_rpm": sample.get("fan1_rpm"),
                "clk_gpu": sample.get("clk_gpu"),
                "clk_mem": sample.get("clk_mem"),
                "power": sample.get("power"),
                "power_limit": sample.get("power_limit"),
                "util_gpu": sample.get("util_gpu"),
                "mem_used_mib": sample.get("mem_used_mib"),
            }
            self._storage.record_sample(db_sample)
        except Exception:
            pass  # never let DB write failures break the sampler thread

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
        # 8 champs attendus depuis _NVIDIA_SMI_QUERY
        if len(parts) < 5 or not parts[0].isdigit():
            return None

        def _int(s, default=0):
            try:
                return int(s)
            except (ValueError, TypeError):
                return default

        def _float(s, default=0.0):
            try:
                return float(s)
            except (ValueError, TypeError):
                return default

        sample = {
            "ts": datetime.datetime.now().strftime("%H:%M:%S"),
            "temp": _int(parts[0]),
            "fan": _int(parts[1]),
            "clk_gpu": _int(parts[2]),
            "clk_mem": _int(parts[3]),
            "power": _float(parts[4]),
        }
        # Champs étendus (peuvent manquer si vieux driver, on parse défensivement)
        if len(parts) > 5: sample["power_limit"] = _float(parts[5])
        if len(parts) > 6: sample["util_gpu"]    = _int(parts[6])
        if len(parts) > 7: sample["mem_used_mib"] = _int(parts[7])

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
