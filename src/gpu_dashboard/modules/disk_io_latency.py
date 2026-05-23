"""Module disk_io_latency — /proc/diskstats latency auditor (R&D #44.1).

/proc/diskstats exposes per-device cumulative-since-boot counters
(17 fields, with discard fields 14-17 since kernel 4.18 and flush
fields 18-19 since 5.5) :

  3  device name
  4  reads completed   5 reads merged
  6  sectors read      7 read ticks (ms total, accumulated wait)
  8  writes completed  9 writes merged
 10  sectors written  11 write ticks (ms)
 12  ios in progress
 13  IO ticks (ms)    14 weighted IO ticks
 15  discards completed   16  merged    17  sectors    18  ticks
 19  flush completed  20  flush ticks

The "ticks" are accumulated wait time for *all* completed I/Os —
divide by the I/O count to get cumulative-since-boot *average*
wait per operation. Coarse, but a clear signal :

  avg_read_wait_ms  = read_ticks / reads_completed
  avg_write_wait_ms = write_ticks / writes_completed

/sys/block/<dev>/inflight gives "<reads_inflight> <writes_inflight>"
right now (instantaneous, no averaging) — useful for spotting
queue saturation on a single sample.

Verdicts (priority-ordered, worst-of across non-loop/ram/dm-/sr
devices) :
  queue_saturated   inflight ≥ 32 on a non-rotational device
                    (NVMe / SSD) → queue depth is full ; subsequent
                    I/Os block.
  read_stall        avg_read_wait_ms ≥ 100 + reads_completed ≥ 1k
                    → reads are slow (slow disk, scrub, dm-crypt
                    flush, mmap thrash).
  write_stall       avg_write_wait_ms ≥ 500 + writes_completed ≥ 1k
                    → writes serialised behind a flush / dm-crypt /
                    wbt throttle.
  ok                no flags ; latencies in the normal range.
  no_block_devices  no non-loop/ram/dm- devices in /proc/diskstats.
  unknown           /proc/diskstats unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "disk_io_latency"


_PROC_DISKSTATS = "/proc/diskstats"
_SYS_BLOCK = "/sys/block"


_SKIP_PREFIXES = ("loop", "ram", "dm-", "fd", "sr")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


_FIELDS = [
    # Index in 0-based row after we skip the first 3 (major, minor, name).
    "reads_completed", "reads_merged", "sectors_read", "read_ticks",
    "writes_completed", "writes_merged", "sectors_written",
    "write_ticks",
    "ios_in_progress", "io_ticks", "weighted_io_ticks",
    "discards_completed", "discards_merged", "sectors_discarded",
    "discard_ticks",
    "flush_completed", "flush_ticks",
]


def parse_diskstats(text: str) -> list:
    """Return [{dev, reads_completed, read_ticks, ...}, ...]."""
    out: list = []
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 14:
            # Need at least 11 counters + major/minor/name = 14.
            continue
        dev = parts[2]
        if any(dev.startswith(p) for p in _SKIP_PREFIXES):
            continue
        # Skip partitions (sda1, sda2) — those duplicate sda totals.
        # Heuristic : if a row's name has a trailing digit AND its
        # stem (without trailing digits) already exists as a row,
        # skip it. We do this in two passes for simplicity.
        try:
            vals = [int(t) for t in parts[3:]]
        except ValueError:
            continue
        row: dict = {"dev": dev}
        for i, name in enumerate(_FIELDS):
            if i < len(vals):
                row[name] = vals[i]
        out.append(row)
    # Filter partitions : keep only rows whose name is NOT a numeric
    # suffix of another row in the same set.
    names = {r["dev"] for r in out}
    filtered: list = []
    for r in out:
        # nvme0n1p1 → stem nvme0n1 ; sda1 → stem sda
        dev = r["dev"]
        m = _partition_stem(dev)
        if m and m in names:
            continue
        filtered.append(r)
    return filtered


def _partition_stem(dev: str) -> Optional[str]:
    """sda1 → sda ; nvme0n1p1 → nvme0n1 ; sda → None."""
    if not dev:
        return None
    if dev.startswith("nvme") and "p" in dev:
        # nvme<N>n<M>p<K> → nvme<N>n<M>
        idx = dev.rfind("p")
        if idx > 0 and dev[idx + 1:].isdigit():
            return dev[:idx]
        return None
    # sda<digit>, sdb<digit>, vda<digit>
    if (len(dev) >= 4 and dev[:3] in ("sda", "sdb", "sdc", "sdd",
                                          "sde", "sdf", "vda", "vdb",
                                          "vdc")):
        suffix = dev[3:]
        if suffix.isdigit():
            return dev[:3]
    return None


def per_device_summary(row: dict) -> dict:
    rc = row.get("reads_completed") or 0
    wc = row.get("writes_completed") or 0
    rt = row.get("read_ticks") or 0
    wt = row.get("write_ticks") or 0
    return {
        "reads_completed": rc,
        "writes_completed": wc,
        "read_ticks_ms": rt,
        "write_ticks_ms": wt,
        "avg_read_wait_ms": (rt / rc) if rc > 0 else 0.0,
        "avg_write_wait_ms": (wt / wc) if wc > 0 else 0.0,
        "ios_in_progress": row.get("ios_in_progress") or 0,
    }


def read_inflight(sys_block: str, dev: str) -> dict:
    text = _read(os.path.join(sys_block, dev, "inflight"))
    if not text:
        return {"read": 0, "write": 0}
    parts = text.split()
    if len(parts) != 2:
        return {"read": 0, "write": 0}
    try:
        return {"read": int(parts[0]), "write": int(parts[1])}
    except ValueError:
        return {"read": 0, "write": 0}


def read_rotational(sys_block: str, dev: str) -> Optional[int]:
    return _read_int(os.path.join(sys_block, dev, "queue",
                                       "rotational"))


_READ_WAIT_MS_THRESHOLD = 100
_WRITE_WAIT_MS_THRESHOLD = 500
_INFLIGHT_THRESHOLD_NVME = 32
_SAMPLE_FLOOR = 1_000


_RECIPE_READ_STALL = (
    "# Average read-wait is high — common causes :\n"
    "#  1. Heavy mmap thrash on a tight-RAM box (cross-ref shipped\n"
    "#     #40.3 vm_tuning_deep + #43.3 zoneinfo_audit for direct\n"
    "#     reclaim).\n"
    "#  2. dm-crypt synchronous read on a wbt-throttled queue\n"
    "#     (shipped #43.2 block_queue_audit).\n"
    "#  3. Background scrub / TRIM running concurrently.\n"
    "# Snapshot per-PID I/O wait to find the offender :\n"
    "iotop -obP   # if installed, else :\n"
    "for p in /proc/[0-9]*; do\n"
    "  io=$(awk '/^read_bytes/{print $2}' $p/io 2>/dev/null)\n"
    "  c=$(cat $p/comm 2>/dev/null)\n"
    "  [ -n \"$io\" ] && [ \"$io\" -gt 1000000 ] && \\\n"
    "    echo \"$p $c read=$io\"\n"
    "done | sort -k3 -n | tail -10"
)

_RECIPE_WRITE_STALL = (
    "# Average write-wait is very high — common causes :\n"
    "#  1. write_cache=\"write through\" on a device that needs\n"
    "#     synchronous flushes (check shipped #43.2).\n"
    "#  2. Writeback throttling (wbt_lat_usec > 0) — disable :\n"
    "echo 0 | sudo tee /sys/block/<DEV>/queue/wbt_lat_usec\n"
    "#  3. dm-crypt with low throughput — kernel is serialising.\n"
    "#  4. SSD GC stalls (rare on modern NVMe, common on QLC SATA).\n"
    "# Snapshot offending PIDs via /proc/<pid>/io 'write_bytes'."
)

_RECIPE_QUEUE_SAT = (
    "# Block queue is saturated (≥ 32 inflight on NVMe-class device).\n"
    "# Bump nr_requests for more in-flight slots :\n"
    "echo 1024 | sudo tee /sys/block/<DEV>/queue/nr_requests\n"
    "# Persist via udev rule (see shipped #43.2 recipe)."
)


def classify(per_dev: list) -> dict:
    if not per_dev:
        return {"verdict": "no_block_devices",
                "reason": ("No non-loop/ram/dm/sr/fd block devices "
                           "in /proc/diskstats."),
                "recommendation": ""}
    # 1) queue saturation (most acute).
    sat = [d for d in per_dev
            if (d.get("inflight_total") or 0)
                >= _INFLIGHT_THRESHOLD_NVME
            and d.get("rotational") == 0]
    if sat:
        names = ", ".join(
            f"{d['dev']} (inflight {d['inflight_total']})"
            for d in sat)
        return {"verdict": "queue_saturated",
                "reason": (f"{len(sat)} NVMe-class device(s) have "
                           f"≥ {_INFLIGHT_THRESHOLD_NVME} inflight "
                           f"I/Os right now. {names}"),
                "recommendation": _RECIPE_QUEUE_SAT}
    # 2) read stall.
    rs = [d for d in per_dev
            if d.get("reads_completed", 0) >= _SAMPLE_FLOOR
            and d.get("avg_read_wait_ms", 0) >= _READ_WAIT_MS_THRESHOLD]
    if rs:
        names = ", ".join(
            f"{d['dev']} ({d['avg_read_wait_ms']:.1f} ms)"
            for d in rs[:5])
        return {"verdict": "read_stall",
                "reason": (f"{len(rs)} device(s) have avg read-wait "
                           f"≥ {_READ_WAIT_MS_THRESHOLD} ms over "
                           f"the lifetime of the host. {names}"),
                "recommendation": _RECIPE_READ_STALL}
    # 3) write stall.
    ws = [d for d in per_dev
            if d.get("writes_completed", 0) >= _SAMPLE_FLOOR
            and d.get("avg_write_wait_ms", 0)
                >= _WRITE_WAIT_MS_THRESHOLD]
    if ws:
        names = ", ".join(
            f"{d['dev']} ({d['avg_write_wait_ms']:.1f} ms)"
            for d in ws[:5])
        return {"verdict": "write_stall",
                "reason": (f"{len(ws)} device(s) have avg write-"
                           f"wait ≥ {_WRITE_WAIT_MS_THRESHOLD} ms. "
                           f"{names}"),
                "recommendation": _RECIPE_WRITE_STALL}
    return {"verdict": "ok",
            "reason": (f"{len(per_dev)} device(s) ; read-wait + "
                       f"write-wait within normal range, no queue "
                       f"saturation."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    text = _read(_PROC_DISKSTATS)
    if text is None:
        return {
            "ok": False,
            "verdict": {"verdict": "unknown",
                         "reason": "/proc/diskstats unreadable.",
                         "recommendation": ""},
            "devices": [],
        }
    raw = parse_diskstats(text)
    devs: list = []
    for r in raw:
        summary = per_device_summary(r)
        inflight = read_inflight(_SYS_BLOCK, r["dev"])
        rot = read_rotational(_SYS_BLOCK, r["dev"])
        devs.append({
            "dev": r["dev"],
            **summary,
            "inflight_read": inflight["read"],
            "inflight_write": inflight["write"],
            "inflight_total": inflight["read"] + inflight["write"],
            "rotational": rot,
        })
    verdict = classify(devs)
    return {
        "ok": True,
        "device_count": len(devs),
        "devices": devs,
        "verdict": verdict,
    }
