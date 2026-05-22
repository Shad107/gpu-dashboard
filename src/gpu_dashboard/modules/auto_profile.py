"""Module auto_profile — automatically switch power profile based on GPU load.

The daemon inspects recent samples from the running MetricsSampler and decides
which named profile (silent / sweet / boost) should be active:

  - silent : sustained <IDLE_THRESHOLD% util (default 5%)         → idle desktop
  - boost  : sustained >BOOST_THRESHOLD% util (default 80%)       → training run
  - sweet  : anything in between                                  → inference / mixed

Switches only when the classification stays stable for ≥ MIN_STABLE seconds, to
avoid flapping on transient peaks.

Pure-function core (`classify_load`) is unit-tested without the daemon.
"""
from __future__ import annotations

import threading
import time as _time
from typing import Optional


NAME = "auto_profile"


def classify_load(
    samples,
    idle_threshold: float = 5.0,
    boost_threshold: float = 80.0,
    min_samples: int = 1,
) -> Optional[str]:
    """Classify a window of samples into 'silent' / 'sweet' / 'boost'.

    Returns None if fewer than `min_samples` are present (no decision).
    Otherwise returns one of the 3 named profiles.
    """
    if not samples:
        return "silent"
    if len(samples) < min_samples:
        return None

    utils = [s.get("util_gpu") or 0 for s in samples]
    avg_util = sum(utils) / len(utils)
    max_util = max(utils)

    # Boost = sustained high util (avg + min both above threshold)
    min_util = min(utils)
    if avg_util >= boost_threshold and min_util >= boost_threshold - 10:
        return "boost"
    # Silent = consistently low (max < idle threshold)
    if max_util <= idle_threshold:
        return "silent"
    # Everything else
    return "sweet"


class AutoProfileDaemon:
    """Daemon thread : every `interval` seconds, look at the last N samples
    and switch profile if the classification has been stable for `min_stable_s`.
    """

    def __init__(
        self,
        sampler,
        api_apply_callback,  # function(profile_name: str) → result dict
        interval: float = 30.0,
        window_seconds: int = 60,
        min_stable_seconds: int = 90,
        idle_threshold: float = 5.0,
        boost_threshold: float = 80.0,
        app_triggers_path: Optional[str] = None,
    ):
        self._sampler = sampler
        self._apply = api_apply_callback
        self._interval = interval
        self._window_s = window_seconds
        self._min_stable_s = min_stable_seconds
        self._idle = idle_threshold
        self._boost = boost_threshold
        self._app_triggers_path = app_triggers_path

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._current_classification: Optional[str] = None
        self._classification_since: float = 0.0
        self._last_applied: Optional[str] = None
        self._last_trigger_match: Optional[str] = None  # last app name that triggered

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="auto-profile")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def status(self) -> dict:
        return {
            "running": self._thread is not None,
            "current_classification": self._current_classification,
            "last_applied": self._last_applied,
            "stable_for_seconds": (
                int(_time.time() - self._classification_since)
                if self._classification_since else 0
            ),
            "trigger_match": self._last_trigger_match,
        }

    def _check_app_triggers(self) -> Optional[str]:
        """Return a profile to force based on running apps, or None.

        Reads triggers from disk on every tick (cheap I/O ; the file is tiny).
        Re-scans /proc/*/comm — also cheap, microseconds on a typical box.
        """
        from . import app_triggers as _at
        triggers = _at.load_triggers(self._app_triggers_path)
        if not triggers:
            self._last_trigger_match = None
            return None
        running = _at.scan_running_apps()
        profile = _at.match_trigger(running, triggers)
        if profile is None:
            self._last_trigger_match = None
            return None
        # Remember the matching key for status() / debug
        for key in triggers:
            for app in running:
                if key.lower() in app.lower():
                    self._last_trigger_match = key
                    break
            else:
                continue
            break
        return profile

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                pass  # never let an exception kill the daemon
            self._stop.wait(self._interval)

    def _tick(self) -> None:
        # ── 1) App triggers — highest priority, no stability gate.
        #    A running blender/llama-server overrides load classification immediately
        #    because the user explicitly told us this app needs a specific profile.
        forced = self._check_app_triggers()
        if forced is not None:
            self._current_classification = forced
            self._classification_since = _time.time()  # reset stability
            if forced != self._last_applied:
                try:
                    self._apply(forced)
                    self._last_applied = forced
                except Exception:
                    pass
            return

        # ── 2) Load-based classification — falls through when no trigger fires.
        buf = self._sampler.snapshot()
        if not buf:
            return
        # Estimate how many tail samples to grab
        try:
            tail_count = max(2, int(self._window_s // max(1, self._sampler.interval)))
        except Exception:
            tail_count = 10
        window = buf[-tail_count:]

        new_class = classify_load(window, self._idle, self._boost, min_samples=2)
        if new_class is None:
            return

        now = _time.time()
        if new_class != self._current_classification:
            self._current_classification = new_class
            self._classification_since = now
            return

        stable_s = now - self._classification_since
        if stable_s >= self._min_stable_s and new_class != self._last_applied:
            try:
                self._apply(new_class)
                self._last_applied = new_class
            except Exception:
                pass
