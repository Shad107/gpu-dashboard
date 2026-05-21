"""Module alert_monitor — fires Telegram alerts when thresholds are breached.

Triggers on consecutive high readings (default 3 in a row) to avoid noise
from transient spikes. Per-kind cooldown (default 5 min) avoids spam.

Public API:
- AlertState — small container tracking last_fired times
- check_thresholds(samples, thresholds, state) → list of {kind, value, threshold, message}
- AlertMonitorDaemon — daemon thread that wraps the check + Telegram dispatch
"""
from __future__ import annotations

import threading
import time as _time
from typing import Optional


NAME = "alert_monitor"

# Alert kinds + which field of a sample they inspect + comparison.
_THRESHOLD_RULES = [
    ("gpu_temp_high",  "temp",         "above"),
    ("mem_temp_high",  "mem_temp",     "above"),
    ("fan_pct_high",   "fan_pct",      "above"),
    ("vram_pct_high",  "_vram_pct",    "above"),  # computed below from mem_used/total
]


class AlertState:
    """Persistent cross-tick state. Tracks when each alert was last fired
    so a cooldown can suppress duplicates."""
    def __init__(self):
        self.last_fired: dict = {}  # kind → timestamp


def check_thresholds(samples, thresholds: dict, state: AlertState) -> list:
    """Inspect the last `min_consecutive` samples, return alerts to fire.

    `thresholds` must include the keys gpu_temp / mem_temp / fan_pct plus
    `min_consecutive` (default 3) and `cooldown_seconds` (default 300).
    """
    if not samples:
        return []

    min_n = int(thresholds.get("min_consecutive", 3))
    cooldown = int(thresholds.get("cooldown_seconds", 300))
    if len(samples) < min_n:
        return []

    # Annotate each sample with a derived _vram_pct = mem_used/mem_total*100
    mem_total = thresholds.get("mem_total_mib")  # optional, needed for VRAM alerts
    tail = samples[-min_n:]
    if mem_total and mem_total > 0:
        tail = [
            {**s, "_vram_pct": (s.get("mem_used_mib") or 0) / mem_total * 100}
            for s in tail
        ]

    now = _time.time()
    alerts = []

    for kind, field, _ in _THRESHOLD_RULES:
        # Map kind → threshold key
        thresh_key = {
            "gpu_temp_high":  "gpu_temp",
            "mem_temp_high":  "mem_temp",
            "fan_pct_high":   "fan_pct",
            "vram_pct_high":  "vram_pct",
        }[kind]
        threshold = thresholds.get(thresh_key)
        if threshold is None:
            continue

        # Extract the field from each sample
        values = []
        all_have_field = True
        for s in tail:
            v = s.get(field)
            if v is None:
                all_have_field = False
                break
            values.append(v)
        if not all_have_field or len(values) < min_n:
            continue

        # All values must be above threshold
        if all(v > threshold for v in values):
            # Cooldown check
            last_fired = state.last_fired.get(kind, 0)
            if now - last_fired < cooldown:
                continue
            current = values[-1]
            alerts.append({
                "kind": kind,
                "value": current,
                "threshold": threshold,
                "message": _format_alert(kind, current, threshold),
            })
            state.last_fired[kind] = now

    return alerts


def _format_alert(kind: str, value, threshold) -> str:
    pretty = {
        "gpu_temp_high":  ("🔥 GPU temp high",      "°C"),
        "mem_temp_high":  ("🔥 VRAM junction high", "°C"),
        "fan_pct_high":   ("🌀 Fan ramped up",      "%"),
        "vram_pct_high":  ("💾 VRAM almost full",   "%"),
    }
    label, unit = pretty.get(kind, (kind, ""))
    # Round numeric values for readability (VRAM% can be a float)
    try:
        v = f"{float(value):.0f}"
    except (ValueError, TypeError):
        v = str(value)
    return f"{label} : {v}{unit} (threshold {threshold}{unit})"


class AlertMonitorDaemon:
    """Watches the sampler buffer at each tick + dispatches Telegram alerts."""

    def __init__(self, sampler, telegram_send_fn, thresholds: dict, interval: float = 30.0):
        self._sampler = sampler
        self._send = telegram_send_fn  # callable(text: str) -> (ok, msg)
        self._thresholds = thresholds
        self._interval = interval
        self._state = AlertState()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="alert-monitor")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                buf = self._sampler.snapshot() if self._sampler else []
                alerts = check_thresholds(buf, self._thresholds, self._state)
                for a in alerts:
                    try:
                        self._send(a["message"])
                    except Exception:
                        pass  # never let Telegram failure crash the daemon
            except Exception:
                pass
            self._stop.wait(self._interval)
