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
        llm_server_url: Optional[str] = None,
    ):
        self.interval = interval
        self._buffer = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._display = nvidia_settings_display
        self._xauth = nvidia_settings_xauthority
        self._storage = storage
        self._llm_url = (llm_server_url or "").rstrip("/")

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
            samples = self._poll_all()
            if samples:
                # Keep only GPU 0 in the live in-memory buffer (back-compat).
                with self._lock:
                    for s in samples:
                        if s.get("gpu_index", 0) == 0:
                            self._buffer.append(s)
                for s in samples:
                    self._persist(s)
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
                "tokens_total_snapshot": sample.get("tokens_total_snapshot"),
                "gpu_index": sample.get("gpu_index", 0),
            }
            self._storage.record_sample(db_sample)
        except Exception:
            pass  # never let DB write failures break the sampler thread

    def _fetch_llm_tokens(self) -> Optional[int]:
        """Fetch cumulative tokens_predicted_total from llama-server /metrics.

        Returns None if no URL configured or server unreachable. Failure must
        never break the sampler thread.
        """
        if not self._llm_url:
            return None
        try:
            import urllib.request
            with urllib.request.urlopen(f"{self._llm_url}/metrics", timeout=2) as r:
                text = r.read().decode("utf-8", errors="replace")
        except Exception:
            return None
        # Parse the line we care about
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            name = parts[0].split("{", 1)[0]
            if name.endswith(":tokens_predicted_total") or name == "tokens_predicted_total":
                try:
                    return int(float(parts[-1]))
                except (ValueError, TypeError):
                    return None
        return None

    def _poll(self) -> Optional[dict]:
        """Back-compat single-GPU poll : returns the FIRST GPU's sample only.

        New code (the _loop) uses _poll_all(). This method is kept for direct
        unit tests of the parsing logic.
        """
        samples = self._poll_all()
        return samples[0] if samples else None

    def _poll_all(self) -> list:
        """Poll nvidia-smi for ALL GPUs. Returns one sample dict per GPU.

        nvidia-smi --query-gpu without -i returns one CSV row per GPU.
        Each row's index (0, 1, 2, ...) becomes the gpu_index field.
        """
        try:
            out = subprocess.run(
                ["nvidia-smi",
                 f"--query-gpu={_NVIDIA_SMI_QUERY}",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            return []
        if out.returncode != 0:
            return []

        def _int(s, default=0):
            try: return int(s)
            except (ValueError, TypeError): return default

        def _float(s, default=0.0):
            try: return float(s)
            except (ValueError, TypeError): return default

        samples = []
        # One CSV row per GPU
        for gpu_index, line in enumerate(out.stdout.strip().splitlines()):
            parts = [p.strip() for p in line.split(",")]
            # 8 champs attendus depuis _NVIDIA_SMI_QUERY
            if len(parts) < 5 or not parts[0].isdigit():
                continue
            sample = {
                "ts": datetime.datetime.now().strftime("%H:%M:%S"),
                "gpu_index": gpu_index,
                "temp": _int(parts[0]),
                "fan": _int(parts[1]),
                "clk_gpu": _int(parts[2]),
                "clk_mem": _int(parts[3]),
                "power": _float(parts[4]),
            }
            if len(parts) > 5: sample["power_limit"] = _float(parts[5])
            if len(parts) > 6: sample["util_gpu"]    = _int(parts[6])
            if len(parts) > 7: sample["mem_used_mib"] = _int(parts[7])
            # Per-fan RPM via nvidia-settings (GPU 0 only — multi-GPU per-fan
            # extraction would need its own loop with /Fan[N] selectors per GPU)
            if gpu_index == 0 and self._display:
                f0, f1 = self._query_per_fan_rpm()
                sample["fan0_rpm"] = f0
                sample["fan1_rpm"] = f1
            # LLM tokens — only attach to GPU 0 since llama-server reports per-server
            if gpu_index == 0 and self._llm_url:
                tokens = self._fetch_llm_tokens()
                if tokens is not None:
                    sample["tokens_total_snapshot"] = tokens
            samples.append(sample)
        return samples

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
