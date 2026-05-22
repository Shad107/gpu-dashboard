"""Module ecc_remap — row-remap delta scheduler (R&D #17.1).

On datacenter cards (A100/H100/etc.) NVIDIA tracks VRAM rows that had to be
remapped due to ECC errors. The counter creeping upward over weeks is a
strong leading indicator (30-90 days) that the card is wearing out.

This module :
  1. Polls nvidia-smi --query-remapped-rows once per scheduled tick
  2. Persists each snapshot in SQLite (history-keyed by UUID + ts)
  3. Computes deltas vs previous snapshot
  4. Flags warn (uncorrectable >= 5) / fail (uncorrectable >= 20 OR
     failure > 0) per NVIDIA's recommendation thresholds
  5. Exports an "RMA report" CSV with serial / uptime / counters

Consumer cards (RTX 3090, etc.) return [N/A] for every column — this
module surfaces that gracefully as 'available=false'.

stdlib only.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
import time
from typing import Optional


NAME = "ecc_remap"

# Persistence file (lightweight history, not the main samples DB to avoid
# bloating /api/state)
_HISTORY_PATH = "~/.config/gpu-dashboard/ecc_remap_history.json"
_HISTORY_MAX = 500


def history_path() -> str:
    return os.path.expanduser(_HISTORY_PATH)


_QUERY_FIELDS = [
    "uuid",
    "name",
    "remapped_rows.correctable",
    "remapped_rows.uncorrectable",
    "remapped_rows.pending",
    "remapped_rows.failure",
    "remapped_rows.histogram.max",
    "remapped_rows.histogram.high",
    "remapped_rows.histogram.partial",
    "remapped_rows.histogram.low",
    "remapped_rows.histogram.none",
]


def _parse_int_or_na(s: str) -> Optional[int]:
    s = s.strip()
    if s.lower() in ("[n/a]", "n/a", "na", "[na]", ""):
        return None
    try:
        return int(s)
    except ValueError:
        return None


def probe() -> list:
    """Run nvidia-smi --query-remapped-rows for every GPU. Returns list of
    per-GPU dicts {uuid, name, correctable, uncorrectable, pending, failure,
    histogram_*, available} or [] if nvidia-smi missing."""
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-remapped-rows=" + ",".join(_QUERY_FIELDS),
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=4,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return []
    if r.returncode != 0 or not r.stdout.strip():
        return []
    out: list = []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        uuid = parts[0]
        name = parts[1]
        cor = _parse_int_or_na(parts[2])
        unc = _parse_int_or_na(parts[3])
        pen = _parse_int_or_na(parts[4])
        fai = _parse_int_or_na(parts[5])
        hist = {
            "max":     _parse_int_or_na(parts[6])  if len(parts) > 6 else None,
            "high":    _parse_int_or_na(parts[7])  if len(parts) > 7 else None,
            "partial": _parse_int_or_na(parts[8])  if len(parts) > 8 else None,
            "low":     _parse_int_or_na(parts[9])  if len(parts) > 9 else None,
            "none":    _parse_int_or_na(parts[10]) if len(parts) > 10 else None,
        }
        available = any(v is not None for v in (cor, unc, pen, fai))
        out.append({
            "uuid": uuid, "name": name,
            "correctable": cor, "uncorrectable": unc,
            "pending": pen, "failure": fai,
            "histogram": hist, "available": available,
        })
    return out


def _verdict(unc: Optional[int], failure: Optional[int]) -> dict:
    """ok / warn / fail based on NVIDIA's published thresholds."""
    if failure is not None and failure > 0:
        return {"kind": "fail", "reason": f"failure count = {failure}"}
    if unc is None:
        return {"kind": "skip", "reason": "card doesn't expose remapped rows"}
    if unc >= 20:
        return {"kind": "fail",
                "reason": f"uncorrectable {unc} >= 20 (NVIDIA RMA threshold)"}
    if unc >= 5:
        return {"kind": "warn",
                "reason": f"uncorrectable {unc} >= 5 (creep watch)"}
    return {"kind": "ok", "reason": f"uncorrectable {unc or 0}"}


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


def save_history(rows: list) -> None:
    p = history_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    rows = rows[-_HISTORY_MAX:]
    with open(p, "w") as f:
        json.dump(rows, f, indent=2)


def record_snapshot(probe_result: Optional[list] = None) -> dict:
    """Take a fresh probe, append to history with timestamp, compute
    deltas vs previous snapshot per UUID."""
    if probe_result is None:
        probe_result = probe()
    now = int(time.time())
    history = load_history()

    # Find each UUID's previous snapshot
    last_by_uuid: dict = {}
    for h in reversed(history):
        uuid = h.get("uuid")
        if uuid and uuid not in last_by_uuid:
            last_by_uuid[uuid] = h

    new_entries: list = []
    for snap in probe_result:
        prev = last_by_uuid.get(snap["uuid"])
        deltas = {}
        if prev:
            for k in ("correctable", "uncorrectable", "pending", "failure"):
                if snap.get(k) is not None and prev.get(k) is not None:
                    deltas[k] = snap[k] - prev[k]
        entry = {
            "ts": now,
            **snap,
            "deltas": deltas,
            "verdict": _verdict(snap.get("uncorrectable"), snap.get("failure")),
        }
        new_entries.append(entry)
        history.append(entry)

    save_history(history)
    return {
        "ok": True,
        "ts": now,
        "snapshots": new_entries,
        "gpus_checked": len(probe_result),
    }


def rma_report_csv(history: Optional[list] = None) -> str:
    """Generate a CSV summary suitable for RMA ticket attachment."""
    if history is None:
        history = load_history()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "first_seen_iso", "last_seen_iso", "uuid", "name",
        "correctable", "uncorrectable", "pending", "failure", "verdict",
    ])
    # Aggregate per UUID
    by_uuid: dict = {}
    for h in history:
        u = h.get("uuid")
        if not u:
            continue
        cur = by_uuid.setdefault(u, {
            "first_ts": h.get("ts"), "last_ts": h.get("ts"),
            "name": h.get("name"), "snapshot": h,
        })
        if h.get("ts", 0) < cur["first_ts"]:
            cur["first_ts"] = h["ts"]
        if h.get("ts", 0) > cur["last_ts"]:
            cur["last_ts"] = h["ts"]
            cur["snapshot"] = h
    import datetime
    for u, rec in by_uuid.items():
        first_iso = datetime.datetime.fromtimestamp(rec["first_ts"]).isoformat(timespec="seconds")
        last_iso = datetime.datetime.fromtimestamp(rec["last_ts"]).isoformat(timespec="seconds")
        s = rec["snapshot"]
        verdict = s.get("verdict", {}).get("kind", "?")
        w.writerow([
            first_iso, last_iso, u, rec["name"] or "",
            s.get("correctable") or "", s.get("uncorrectable") or "",
            s.get("pending") or "", s.get("failure") or "", verdict,
        ])
    return buf.getvalue()


def status() -> dict:
    """Top-level snapshot + recent history for the UI."""
    history = load_history()
    latest = probe()
    return {
        "ok": True,
        "live": latest,
        "history": history[-50:],
        "history_count": len(history),
        "any_card_exposes_ecc": any(s.get("available") for s in latest),
    }
