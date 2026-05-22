"""Module pcie_histogram — PCIe link-state thrasher histogram (R&D #18.6).

The existing hot_swap module (R&D #14.5) detects each PCIe
renegotiation as a one-shot event. This module aggregates those
events over a sliding window and surfaces three things :

  1. Histogram bucket counts — Gen1 ×4, Gen3 ×16, Gen4 ×16, etc.
  2. Transitions-per-minute over the last hour
  3. Thrash verdict : "stable" / "intermittent" / "thrashing"

Particularly useful for OcuLink / eGPU rigs where the cable
quality can cause Gen3 ↔ Gen4 flapping under load.

Reads events from hot_swap state file ; pure aggregation.
"""
from __future__ import annotations

import time
from typing import Optional


NAME = "pcie_histogram"


# Map (speed_gts, width) to a human bucket name.
def link_bucket(speed_gts: Optional[float], width: Optional[int]) -> str:
    if speed_gts is None or width is None:
        return "unknown"
    # PCIe generation from GT/s
    gen_map = {2.5: 1, 5.0: 2, 8.0: 3, 16.0: 4, 32.0: 5, 64.0: 6}
    gen = gen_map.get(round(speed_gts, 1), 0)
    if gen == 0:
        return f"{speed_gts}GT/s x{width}"
    return f"Gen{gen} x{width}"


def _parse_link_str(s: Optional[str]) -> Optional[float]:
    """'8.0 GT/s PCIe' → 8.0."""
    if not s:
        return None
    head = s.split()[0]
    try:
        return float(head)
    except (ValueError, IndexError):
        return None


def _parse_width(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    try:
        return int(s.strip())
    except ValueError:
        return None


def build_histogram(events: list, window_s: int = 3600,
                     now_ts: Optional[float] = None) -> dict:
    """Aggregate a list of hot_swap events into a histogram.

    Each event is expected to have shape :
      {kind: 'link_change', ts: int, target: '0000:01:00.0',
       before: {speed: '8.0 GT/s PCIe', width: '16'},
       after:  {speed: '16.0 GT/s PCIe', width: '16'}}

    Returns {
      window_s, transition_count, transitions_per_min,
      buckets: {bucket_name: hit_count},
      verdict: 'stable' | 'intermittent' | 'thrashing',
      first_event_ts, last_event_ts,
    }
    """
    if now_ts is None:
        now_ts = time.time()
    cutoff = now_ts - window_s
    recent = [e for e in events
              if e.get("kind") == "link_change" and e.get("ts", 0) >= cutoff]
    buckets: dict = {}
    first_ts: Optional[int] = None
    last_ts: Optional[int] = None
    for e in recent:
        ts = e.get("ts")
        if first_ts is None or ts < first_ts:
            first_ts = ts
        if last_ts is None or ts > last_ts:
            last_ts = ts
        after = e.get("after") or {}
        bucket = link_bucket(_parse_link_str(after.get("speed")),
                              _parse_width(after.get("width")))
        buckets[bucket] = buckets.get(bucket, 0) + 1
    tcount = len(recent)
    minutes = max(1, window_s / 60)
    tpm = tcount / minutes
    verdict = _verdict_from_tpm(tpm)
    return {
        "window_s": window_s,
        "transition_count": tcount,
        "transitions_per_min": round(tpm, 2),
        "buckets": buckets,
        "verdict": verdict,
        "first_event_ts": first_ts,
        "last_event_ts": last_ts,
    }


def _verdict_from_tpm(tpm: float) -> str:
    if tpm < 0.1:
        return "stable"
    if tpm < 1.0:
        return "intermittent"
    return "thrashing"


def status(cfg=None) -> dict:
    """Aggregate snapshot. Pulls events from hot_swap state."""
    from . import hot_swap
    state = hot_swap.load_state()
    events = state.get("events", []) if isinstance(state, dict) else []
    hist_1h = build_histogram(events, window_s=3600)
    hist_24h = build_histogram(events, window_s=86400)
    return {
        "ok": True,
        "histogram_1h": hist_1h,
        "histogram_24h": hist_24h,
        "total_events_seen": len(events),
    }
