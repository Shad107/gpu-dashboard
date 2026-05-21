"""Background retention task : purge old samples + events, run VACUUM weekly.

Runs as a daemon thread alongside the MetricsSampler. Both share the same
Storage instance (which serializes writes via its internal lock).

- Purge runs hourly: delete samples + events older than RETENTION_DAYS
- VACUUM runs weekly: compact the SQLite file after a chunk of purges

Logs (counts purged) go to stderr — silent in normal operation.
"""
from __future__ import annotations

import sys
import threading
import time as _time
from typing import Optional


class RetentionTask:
    """Daemon thread that periodically purges old DB rows and vacuums."""

    PURGE_INTERVAL_SECONDS = 3600          # hourly
    VACUUM_INTERVAL_SECONDS = 7 * 86400    # weekly

    def __init__(self, storage, retention_days: int = 30):
        self._storage = storage
        self._retention_days = max(1, int(retention_days))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_vacuum_ts = 0.0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="gpu-retention"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def tick_now(self) -> tuple:
        """Run one purge cycle synchronously. Returns (samples_deleted, events_deleted).

        Exposed for tests and the /api/admin/purge endpoint (future).
        """
        try:
            return self._storage.purge_older_than(self._retention_days)
        except Exception as e:
            print(f"retention: purge failed: {e}", file=sys.stderr)
            return (0, 0)

    def vacuum_now(self) -> None:
        """Compact SQLite. Safe to call alone."""
        try:
            self._storage.vacuum()
            self._last_vacuum_ts = _time.time()
        except Exception as e:
            print(f"retention: vacuum failed: {e}", file=sys.stderr)

    def _loop(self) -> None:
        # First tick a few seconds after start to avoid stampede at boot
        self._stop.wait(min(60, self.PURGE_INTERVAL_SECONDS))
        while not self._stop.is_set():
            self.tick_now()
            now = _time.time()
            if now - self._last_vacuum_ts >= self.VACUUM_INTERVAL_SECONDS:
                self.vacuum_now()
            self._stop.wait(self.PURGE_INTERVAL_SECONDS)
