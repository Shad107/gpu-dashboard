"""Module pcie_aer — PCIe Advanced Error Reporting counter (R&D #24.2).

The PCIe link can fail in three tiers (correctable, non-fatal,
fatal). Correctable errors are by design transparent — the device
retries — but a steady drip of `BadTLP` or `RxErr` on a single GPU
is the strongest leading indicator of a bad PCIe cable, a marginal
riser, or a failing slot. The shipped PCIe link-thrasher (#18.6)
and ASPM audit (#23.4) cover the consequences ; this module catches
the *root cause* earlier.

Reads /sys/bus/pci/devices/<bdf>/aer_dev_{correctable,fatal,nonfatal}
for every NVIDIA GPU device. Pure sysfs, no sudo, no extra deps.

Baselines counter values per BDF on first observation. Subsequent
calls compute deltas. Verdicts :

  - clean              (all counters at baseline)
  - low_correctable    (≤10 correctable since baseline — usually fine)
  - high_correctable   (>10 correctable — cable or seating)
  - non_fatal          (any non-fatal — escalating issue)
  - fatal              (any fatal — link likely dropped)

stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional


NAME = "pcie_aer"


_BASELINE_PATH = "~/.config/gpu-dashboard/pcie_aer_baseline.json"

# Fields whose `TOTAL_ERR_*` line summarizes the whole category
_TOTAL_LINE_PATTERN = re.compile(r"^TOTAL_ERR_(\w+)\s+(\d+)$")


def baseline_path() -> str:
    return os.path.expanduser(_BASELINE_PATH)


def load_baseline() -> dict:
    p = baseline_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_baseline(data: dict) -> None:
    p = baseline_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f, indent=2)


def parse_aer_file(text: str) -> dict:
    """Each line is '<name> <count>'. Returns {name: count}."""
    out: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.rsplit(maxsplit=1)
        if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
            continue
        out[parts[0]] = int(parts[1])
    return out


def read_aer_counters(bdf: str,
                      sys_root: str = "/sys/bus/pci/devices") -> dict:
    """Return {correctable: {...}, fatal: {...}, nonfatal: {...}} for one BDF."""
    out: dict = {}
    for tier, fname in (("correctable", "aer_dev_correctable"),
                          ("fatal", "aer_dev_fatal"),
                          ("nonfatal", "aer_dev_nonfatal")):
        p = os.path.join(sys_root, bdf, fname)
        try:
            with open(p) as f:
                txt = f.read()
            out[tier] = parse_aer_file(txt)
        except OSError:
            out[tier] = {}
    return out


def total_for_tier(tier_dict: dict) -> int:
    """Pick TOTAL_ERR_* if present, else sum all entries."""
    for k, v in tier_dict.items():
        if k.startswith("TOTAL_ERR_"):
            return v
    return sum(v for v in tier_dict.values() if isinstance(v, int))


def list_nvidia_bdfs(sys_root: str = "/sys/bus/pci/devices") -> list[str]:
    out: list[str] = []
    try:
        for name in sorted(os.listdir(sys_root)):
            vpath = os.path.join(sys_root, name, "vendor")
            try:
                with open(vpath) as f:
                    if f.read().strip().lower() == "0x10de":
                        out.append(name)
            except OSError:
                continue
    except OSError:
        pass
    return out


def compute_delta(prev: dict, curr: dict) -> dict:
    """For each tier, return {field: delta} where delta = curr - prev."""
    out: dict = {}
    for tier in ("correctable", "fatal", "nonfatal"):
        prev_t = prev.get(tier, {}) if isinstance(prev, dict) else {}
        curr_t = curr.get(tier, {}) if isinstance(curr, dict) else {}
        deltas: dict = {}
        for k, v in curr_t.items():
            d = v - prev_t.get(k, 0)
            if d > 0:
                deltas[k] = d
        out[tier] = deltas
    return out


def classify(delta: dict) -> dict:
    """Verdict from the per-tier deltas."""
    fatal = total_for_tier(delta.get("fatal", {}))
    nonfatal = total_for_tier(delta.get("nonfatal", {}))
    correctable = total_for_tier(delta.get("correctable", {}))
    if fatal > 0:
        return {"verdict": "fatal",
                "reason": (f"{fatal} fatal AER event(s) since baseline. "
                           "Link almost certainly dropped at least once."),
                "recovery": ("Inspect dmesg for the slot/cable. Reseat / "
                              "swap riser. Consider RMA if persistent.")}
    if nonfatal > 0:
        return {"verdict": "non_fatal",
                "reason": (f"{nonfatal} non-fatal AER event(s) since baseline. "
                           "Escalating issue — investigate before it becomes "
                           "fatal."),
                "recovery": "lspci -vv -s <bdf> ; check riser/cable."}
    if correctable > 10:
        return {"verdict": "high_correctable",
                "reason": (f"{correctable} correctable error(s) since "
                           "baseline. Steady drip points to marginal cable "
                           "or weak signal integrity."),
                "recovery": ("Swap OcuLink / PCIe riser cable. If on a "
                              "mobo slot, try a different one.")}
    if correctable > 0:
        return {"verdict": "low_correctable",
                "reason": (f"{correctable} correctable error(s). PCIe "
                           "auto-recovers ; nothing actionable yet."),
                "recovery": ""}
    return {"verdict": "clean",
            "reason": "No AER counters changed since baseline.",
            "recovery": ""}


def status(cfg=None) -> dict:
    """Aggregate snapshot. Auto-baselines on first call per BDF."""
    baseline = load_baseline()
    bdfs = list_nvidia_bdfs()
    per_dev: list = []
    new_baseline: dict = dict(baseline)
    worst_verdict = "clean"
    aggregate_delta: dict = {"correctable": {}, "fatal": {}, "nonfatal": {}}
    for bdf in bdfs:
        curr = read_aer_counters(bdf)
        prev = baseline.get(bdf)
        if prev is None:
            new_baseline[bdf] = {
                "first_seen_ts": int(time.time()),
                "counters": curr,
            }
            delta = {"correctable": {}, "fatal": {}, "nonfatal": {}}
            first_seen = True
        else:
            delta = compute_delta(prev.get("counters", {}), curr)
            first_seen = False
        verdict = classify(delta)
        # Track worst across devices
        rank = {"clean": 0, "low_correctable": 1, "high_correctable": 2,
                "non_fatal": 3, "fatal": 4}
        if rank.get(verdict["verdict"], 0) > rank.get(worst_verdict, 0):
            worst_verdict = verdict["verdict"]
        per_dev.append({
            "bdf": bdf,
            "totals": {
                "correctable": total_for_tier(curr.get("correctable", {})),
                "fatal": total_for_tier(curr.get("fatal", {})),
                "nonfatal": total_for_tier(curr.get("nonfatal", {})),
            },
            "delta": delta,
            "first_seen": first_seen,
            "verdict": verdict,
        })
        # Roll up aggregate deltas
        for tier in aggregate_delta:
            for k, v in delta.get(tier, {}).items():
                aggregate_delta[tier][k] = aggregate_delta[tier].get(k, 0) + v
    if new_baseline != baseline:
        save_baseline(new_baseline)
    summary = classify(aggregate_delta) if bdfs else {
        "verdict": "no_gpus",
        "reason": "No NVIDIA PCI devices found.",
        "recovery": "",
    }
    return {
        "ok": True,
        "devices": per_dev,
        "device_count": len(per_dev),
        "aggregate_delta": aggregate_delta,
        "verdict": summary,
    }
