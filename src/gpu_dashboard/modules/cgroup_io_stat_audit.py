"""Module cgroup_io_stat_audit — per-cgroup IO activity +
pressure (R&D #81.3).

Reads every reachable /sys/fs/cgroup/**/io.stat and the
root /sys/fs/cgroup/io.pressure to surface which cgroup is
hogging the NVMe and whether the kernel is currently
throttling under that pressure.

Why this matters on a homelab box :

  Existing cgroup_cpuio audits cpu.weight + io.weight (the
  priority knob).  psi_pressure_audit reads cpu/mem/io
  pressure only at the root.  Neither answers "which slice
  is hogging the NVMe right now ?" — the top
  homelab debugging question when a Docker build, ZFS
  scrub, or Jellyfin transcode silently kills latency.

The verdict combines two signals :
  * io.pressure (system-wide kernel-side throttling — the
    "is this happening NOW" indicator),
  * per-cgroup io.stat write totals (which cgroup is at
    fault).

Verdicts (worst first) :

  runaway_writer        io.pressure full avg10 > 30 %
                        AND one cgroup contributes > 80 %
                        of total writes since boot.
  io_throttled_long     io.pressure full avg300 > 10 %
                        (sustained moderate throttling).
  imbalanced_readers    one cgroup > 80 % of total reads
                        since boot (informational — large
                        builds, scrubs).
  ok_balanced           pressure low, IO spread reasonably.
  no_cgroup_v2          /sys/fs/cgroup not unified-v2 or
                        io controller not delegated.
  unknown               /sys/fs/cgroup absent.
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_CGROUP_ROOT = "/sys/fs/cgroup"

# Thresholds
_PRESSURE_AVG10_ERR = 30.0
_PRESSURE_AVG300_WARN = 10.0
_WRITE_DOMINANCE = 0.80
_READ_DOMINANCE = 0.80
_MIN_WRITES_GB = 1.0          # ignore cgroup dominance on cold systems

_PRESSURE_LINE_RE = re.compile(
    r"^(some|full)\s+"
    r"avg10=(\d+\.\d+)\s+"
    r"avg60=(\d+\.\d+)\s+"
    r"avg300=(\d+\.\d+)\s+"
    r"total=(\d+)\s*$")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_io_stat(text: str) -> dict:
    """Parses /sys/fs/cgroup/<cg>/io.stat (per-device).

    Returns dict {major_minor: {rbytes,wbytes,rios,wios,dbytes,dios}}.
    """
    out: dict = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        dev = parts[0]
        if not re.match(r"^\d+:\d+$", dev):
            continue
        d: dict = {}
        for kv in parts[1:]:
            if "=" not in kv:
                continue
            k, v = kv.split("=", 1)
            try:
                d[k] = int(v)
            except ValueError:
                continue
        out[dev] = d
    return out


def parse_pressure(text: str) -> dict:
    """Parses /sys/fs/cgroup/io.pressure (or any *.pressure).

    Returns {some: {avg10, avg60, avg300, total},
              full: {...}}."""
    out: dict = {}
    for line in text.splitlines():
        m = _PRESSURE_LINE_RE.match(line)
        if m is None:
            continue
        out[m.group(1)] = {
            "avg10": float(m.group(2)),
            "avg60": float(m.group(3)),
            "avg300": float(m.group(4)),
            "total": int(m.group(5)),
        }
    return out


def walk_io_stats(root: str = DEFAULT_CGROUP_ROOT
                   ) -> list[dict]:
    """Returns list of {path, totals} per cgroup with an
    io.stat file. totals aggregates across devices."""
    rows: list[dict] = []
    try:
        for dirpath, dirnames, filenames in os.walk(
                root, followlinks=False):
            if "io.stat" not in filenames:
                continue
            text = _read_text(os.path.join(dirpath, "io.stat"))
            if text is None:
                continue
            per_dev = parse_io_stat(text)
            totals = {
                "rbytes": sum(d.get("rbytes", 0)
                                for d in per_dev.values()),
                "wbytes": sum(d.get("wbytes", 0)
                                for d in per_dev.values()),
                "dbytes": sum(d.get("dbytes", 0)
                                for d in per_dev.values()),
                "rios": sum(d.get("rios", 0)
                              for d in per_dev.values()),
                "wios": sum(d.get("wios", 0)
                              for d in per_dev.values()),
            }
            rel = (
                "/" if dirpath == root
                else dirpath[len(root):])
            rows.append({"path": rel, "totals": totals})
    except (OSError, PermissionError):
        return []
    return rows


def is_cgroup_v2(root: str = DEFAULT_CGROUP_ROOT) -> bool:
    controllers = _read_text(
        os.path.join(root, "cgroup.controllers"))
    if controllers is None:
        return False
    return "io" in controllers.split()


def classify(v2: bool,
             root_pressure: Optional[dict],
             cgroups: list[dict],
             root_exists: bool) -> dict:
    if not root_exists:
        return {"verdict": "unknown",
                "reason": "/sys/fs/cgroup absent."}
    if not v2:
        return {"verdict": "no_cgroup_v2",
                "reason": (
                    "cgroup-v2 unified hierarchy not in use "
                    "or io controller not delegated.")}

    if not cgroups:
        return {"verdict": "unknown",
                "reason": "No io.stat files found."}

    # Pull root totals for relative comparison
    root_row = next((r for r in cgroups if r["path"] == "/"),
                     None)
    non_root = [r for r in cgroups if r["path"] != "/"]

    full_avg10 = (
        (root_pressure or {}).get("full", {}).get("avg10", 0.0))
    full_avg300 = (
        (root_pressure or {}).get("full", {}).get("avg300", 0.0))

    # Compute top writer
    top_writer = None
    if non_root:
        top_writer = max(non_root,
                          key=lambda r: r["totals"]["wbytes"])
    total_wbytes = root_row["totals"]["wbytes"] if root_row else 0
    write_ratio = (
        top_writer["totals"]["wbytes"] / total_wbytes
        if (top_writer and total_wbytes > 0)
        else 0.0)
    total_writes_gb = total_wbytes / (1024 ** 3)

    # 1. err — runaway writer + active pressure
    if (full_avg10 > _PRESSURE_AVG10_ERR
            and top_writer is not None
            and write_ratio > _WRITE_DOMINANCE
            and total_writes_gb > _MIN_WRITES_GB):
        return {"verdict": "runaway_writer",
                "reason": (
                    f"io.pressure full avg10 "
                    f"{full_avg10:.1f} % and cgroup "
                    f"{top_writer['path']} owns "
                    f"{write_ratio:.0%} of "
                    f"{total_writes_gb:.1f} GiB writes."),
                "top_writer": top_writer["path"],
                "write_ratio": write_ratio,
                "full_avg10": full_avg10}

    # 2. warn — sustained pressure
    if full_avg300 > _PRESSURE_AVG300_WARN:
        return {"verdict": "io_throttled_long",
                "reason": (
                    f"io.pressure full avg300 "
                    f"{full_avg300:.1f} % — sustained "
                    "moderate IO throttling."),
                "full_avg300": full_avg300}

    # 3. accent — read dominance
    top_reader = (
        max(non_root, key=lambda r: r["totals"]["rbytes"])
        if non_root else None)
    total_rbytes = root_row["totals"]["rbytes"] if root_row else 0
    read_ratio = (
        top_reader["totals"]["rbytes"] / total_rbytes
        if (top_reader and total_rbytes > 0)
        else 0.0)
    if (top_reader is not None
            and read_ratio > _READ_DOMINANCE
            and total_rbytes > 1024**3):
        return {"verdict": "imbalanced_readers",
                "reason": (
                    f"cgroup {top_reader['path']} "
                    f"contributed {read_ratio:.0%} of "
                    f"{total_rbytes / (1024**3):.1f} GiB "
                    "reads since boot."),
                "top_reader": top_reader["path"],
                "read_ratio": read_ratio}

    # 4. ok_balanced
    return {"verdict": "ok_balanced",
            "reason": (
                f"{len(cgroups)} cgroup(s) audited ; "
                f"io.pressure full avg10="
                f"{full_avg10:.2f} %, "
                f"avg300={full_avg300:.2f} %.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_CGROUP_ROOT) -> dict:
    root_exists = os.path.isdir(root)
    v2 = is_cgroup_v2(root) if root_exists else False
    pressure_text = _read_text(os.path.join(root, "io.pressure"))
    root_pressure = (
        parse_pressure(pressure_text)
        if pressure_text else None)
    cgroups = walk_io_stats(root) if v2 else []
    verdict = classify(v2, root_pressure, cgroups, root_exists)

    # Build a small sample for the UI (top 5 by writes)
    non_root = [r for r in cgroups if r["path"] != "/"]
    top_writers = sorted(
        non_root,
        key=lambda r: r["totals"]["wbytes"],
        reverse=True)[:5]
    return {
        "ok": verdict["verdict"] not in (
            "unknown", "runaway_writer"),
        "cgroup_count": len(cgroups),
        "root_pressure": root_pressure,
        "top_writers": [
            {"path": w["path"],
             "wbytes": w["totals"]["wbytes"],
             "rbytes": w["totals"]["rbytes"]}
            for w in top_writers],
        "verdict": verdict,
    }
