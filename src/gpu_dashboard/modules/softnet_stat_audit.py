"""Module softnet_stat_audit — per-CPU softirq packet
processing health (R&D #79.1).

Reads /proc/net/softnet_stat — one row per CPU, 11-15 hex
columns documented in net/core/net-procfs.c :

  col 0  total            packets processed by this CPU
  col 1  dropped          softirq-level buffer overflow
  col 2  time_squeeze     softirq ran out of budget before
                          completing all work
  col 8  cpu_collision    rare, indicates lock contention
  col 9  received_rps     packets delivered via RPS
  col 10 flow_limit_count flow limiter trips
  col 11 softnet_backlog_len  (newer kernels)
  col 12 cpu_index            (newer kernels)
  col 13 percpu_alloc_fail    (newer kernels)

Why this matters on a single-GPU homelab box :

  * softnet drops are invisible to ethtool / `ip -s` — they
    happen *above* the driver, between NAPI poll and the
    socket. The classic symptom is "ssh micro-stall every
    few seconds while llama.cpp pushes batches".
  * time_squeeze hits when the NAPI budget (default 64)
    is too small for the traffic rate ; bumping
    net.core.netdev_budget fixes it.
  * cpu_collision should always be zero on a modern kernel ;
    non-zero means SMP locking pathology.

Verdicts (worst first) :

  err     drops on ≥2 CPUs, OR drop/processed ratio > 0.1%
          on any CPU (real packet loss happening now).
  warn    drops on a single CPU, OR any time_squeeze > 1000
          (NAPI budget exhausted often).
  accent  any cpu_collision > 0 (rare modern-kernel signal).
  ok      no drops, all time_squeezes < 1000.
  unknown /proc/net/softnet_stat missing or empty.
"""
from __future__ import annotations

import os
from typing import Optional

DEFAULT_PATH = "/proc/net/softnet_stat"

# Field indices in the hex-column row
_COL_PROCESSED = 0
_COL_DROPPED = 1
_COL_TIME_SQUEEZE = 2
_COL_CPU_COLLISION = 8

# Thresholds
_TS_NOISY_FLOOR = 1000          # time_squeeze warn floor
_DROP_RATIO_ERR = 0.001         # 0.1 % drop/processed


def parse(text: str) -> list[dict]:
    """Parse softnet_stat text into per-CPU dict rows.

    Each row dict has keys : cpu, processed, dropped,
    time_squeeze, cpu_collision.  Returns [] if blank.
    """
    rows: list[dict] = []
    for cpu, line in enumerate(text.strip().splitlines()):
        cols = line.split()
        if len(cols) < 3:
            continue
        try:
            processed = int(cols[_COL_PROCESSED], 16)
            dropped = int(cols[_COL_DROPPED], 16)
            time_squeeze = int(cols[_COL_TIME_SQUEEZE], 16)
            cpu_collision = (
                int(cols[_COL_CPU_COLLISION], 16)
                if len(cols) > _COL_CPU_COLLISION else 0)
        except ValueError:
            continue
        rows.append({
            "cpu": cpu,
            "processed": processed,
            "dropped": dropped,
            "time_squeeze": time_squeeze,
            "cpu_collision": cpu_collision,
        })
    return rows


def read_softnet_stat(path: str = DEFAULT_PATH
                       ) -> Optional[list[dict]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, PermissionError):
        return None
    return parse(text)


def classify(rows: Optional[list[dict]]) -> dict:
    if rows is None:
        return {"verdict": "unknown",
                "reason": "/proc/net/softnet_stat missing."}
    if not rows:
        return {"verdict": "unknown",
                "reason": "/proc/net/softnet_stat empty."}

    drops = [r for r in rows if r["dropped"] > 0]

    # Severe : drops + high ratio
    for r in rows:
        if r["processed"] > 0:
            ratio = r["dropped"] / r["processed"]
            if ratio > _DROP_RATIO_ERR:
                return {
                    "verdict": "err",
                    "reason": (
                        f"CPU{r['cpu']} drop ratio "
                        f"{ratio:.2%} ({r['dropped']} of "
                        f"{r['processed']}) — real packet "
                        "loss happening.")}
    if len(drops) >= 2:
        return {
            "verdict": "err",
            "reason": (
                f"{len(drops)} CPU(s) showing softirq drops "
                "— overloaded backlog/RPS buffers.")}

    if len(drops) == 1:
        r = drops[0]
        return {
            "verdict": "warn",
            "reason": (
                f"CPU{r['cpu']} has {r['dropped']} softirq "
                "drop(s) — single-CPU backlog overflow.")}

    bad_ts = [r for r in rows
              if r["time_squeeze"] > _TS_NOISY_FLOOR]
    if bad_ts:
        worst = max(bad_ts, key=lambda r: r["time_squeeze"])
        return {
            "verdict": "warn",
            "reason": (
                f"CPU{worst['cpu']} time_squeeze "
                f"{worst['time_squeeze']} — NAPI budget "
                "exhausted often.")}

    colls = [r for r in rows if r["cpu_collision"] > 0]
    if colls:
        return {
            "verdict": "accent",
            "reason": (
                f"{len(colls)} CPU(s) saw cpu_collision — "
                "lock contention in NAPI path.")}

    return {"verdict": "ok",
            "reason": (
                f"{len(rows)} CPU(s) audited ; no drops, no "
                "NAPI squeeze, no collisions.")}


def status(config: Optional[dict] = None,
           path: str = DEFAULT_PATH) -> dict:
    rows = read_softnet_stat(path)
    verdict = classify(rows)
    return {
        "ok": verdict["verdict"] not in (
            "err", "unknown"),
        "cpu_count": len(rows) if rows else 0,
        "totals": {
            "dropped": sum(r["dropped"] for r in (rows or [])),
            "time_squeeze": sum(
                r["time_squeeze"] for r in (rows or [])),
            "cpu_collision": sum(
                r["cpu_collision"] for r in (rows or [])),
            "processed": sum(
                r["processed"] for r in (rows or [])),
        },
        "rows": rows or [],
        "verdict": verdict,
    }
