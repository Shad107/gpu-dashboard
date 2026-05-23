"""Module proc_static_audit — Per-boot static PCI auditor (R&D #26.1).

The shipped procfs deep-state diff (R&D #23.6) watches the
*dynamic* /proc/driver/nvidia/gpus/<bdf>/information fields (GPU
Excluded, GSP firmware, etc.). This module watches the truly
*static* PCI facts that should NEVER change unless the card was
physically reseated, swapped, or the BIOS / partner-card vBIOS
quirks reshuffled BARs.

Static fingerprint (sha256) over :

  - vendor / device / subsystem_vendor / subsystem_device / revision
  - BAR resource layout (`resource` file rows)
  - IRQ number
  - boot_vga flag

When the fingerprint changes between calls :

  - Same UUID, new BARs       → partner-card vBIOS reshuffle
  - Same UUID, new IRQ        → ACPI re-enumeration (common on
                                 laptop dock attach)
  - Same UUID, new subsystem  → ROM swap (used-card scams,
                                  vBIOS re-flash)

Baselines per (BDF, vendor:device) on first observation.

stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Optional


NAME = "proc_static_audit"


_PCI_ROOT = "/sys/bus/pci/devices"
_BASELINE_PATH = "~/.config/gpu-dashboard/proc_static_baseline.json"

# Fields to fingerprint (always present in modern kernels)
STATIC_FIELDS = ("vendor", "device", "subsystem_vendor", "subsystem_device",
                  "revision", "irq", "boot_vga")


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


def read_attr(bdf: str, attr: str,
                sys_root: str = _PCI_ROOT) -> Optional[str]:
    p = os.path.join(sys_root, bdf, attr)
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def read_resource(bdf: str,
                    sys_root: str = _PCI_ROOT) -> Optional[list[str]]:
    """Return the non-zero rows of /sys/bus/pci/devices/<bdf>/resource."""
    p = os.path.join(sys_root, bdf, "resource")
    try:
        with open(p) as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    # Each row is "start end flags" — keep only non-zero rows
    interesting: list[str] = []
    for ln in lines:
        if not ln.strip():
            continue
        parts = ln.split()
        if len(parts) < 3:
            continue
        # Skip all-zero rows (unused BARs)
        if all(p == "0x0000000000000000" or p == "0x0" for p in parts):
            continue
        interesting.append(" ".join(parts))
    return interesting


def list_nvidia_bdfs(sys_root: str = _PCI_ROOT) -> list[str]:
    out: list[str] = []
    try:
        for name in sorted(os.listdir(sys_root)):
            v = read_attr(name, "vendor", sys_root)
            if v and v.lower() == "0x10de":
                out.append(name)
    except OSError:
        return []
    return out


def collect_static(bdf: str, sys_root: str = _PCI_ROOT) -> dict:
    """Read all tracked static attrs + resource layout."""
    attrs: dict = {}
    for f in STATIC_FIELDS:
        attrs[f] = read_attr(bdf, f, sys_root)
    attrs["resource"] = read_resource(bdf, sys_root) or []
    return attrs


def fingerprint(static: dict) -> str:
    """Deterministic sha256 over the static attrs + sorted resource rows."""
    payload: list = []
    for f in STATIC_FIELDS:
        payload.append(f"{f}={static.get(f)}")
    for row in static.get("resource", []):
        payload.append(f"res={row}")
    joined = "|".join(payload)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def diff_attrs(prev: dict, curr: dict) -> list[dict]:
    """Per-field deltas between baseline and current static dict."""
    out: list[dict] = []
    for f in STATIC_FIELDS:
        if prev.get(f) != curr.get(f):
            out.append({"field": f,
                         "before": prev.get(f),
                         "after": curr.get(f)})
    prev_r = prev.get("resource", [])
    curr_r = curr.get("resource", [])
    if prev_r != curr_r:
        out.append({"field": "resource",
                     "before_rows": len(prev_r),
                     "after_rows": len(curr_r),
                     "changed_count": _count_diff_rows(prev_r, curr_r)})
    return out


def _count_diff_rows(a: list, b: list) -> int:
    n = max(len(a), len(b))
    differ = 0
    for i in range(n):
        if i >= len(a) or i >= len(b) or a[i] != b[i]:
            differ += 1
    return differ


def classify(diffs: list[dict]) -> dict:
    """Pick the most concerning category."""
    if not diffs:
        return {"verdict": "clean",
                "reason": "No static attrs drifted since baseline.",
                "severity": "info"}
    fields = {d["field"] for d in diffs}
    if fields & {"subsystem_vendor", "subsystem_device"}:
        return {"verdict": "subsystem_swap",
                "reason": ("PCI subsystem ID changed — partner vendor "
                           "swapped or ROM re-flashed. Verify card identity."),
                "severity": "critical"}
    if "vendor" in fields or "device" in fields or "revision" in fields:
        return {"verdict": "identity_swap",
                "reason": ("PCI vendor/device/revision changed — different "
                           "card in the same slot."),
                "severity": "critical"}
    if "resource" in fields:
        return {"verdict": "bar_reshuffle",
                "reason": ("BAR layout changed — BIOS / ACPI rebooted "
                           "with different memory map."),
                "severity": "warn"}
    if "irq" in fields:
        return {"verdict": "irq_changed",
                "reason": ("IRQ changed — ACPI re-enumeration "
                           "(common on laptop dock attach)."),
                "severity": "warn"}
    return {"verdict": "minor_drift",
            "reason": ("Minor static-attr drift "
                       f"({', '.join(sorted(fields))})."),
            "severity": "info"}


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    bdfs = list_nvidia_bdfs()
    baseline = load_baseline()
    per_card: list = []
    worst_severity = "info"
    rank = {"info": 0, "warn": 1, "critical": 2}
    new_baseline = dict(baseline)
    for bdf in bdfs:
        curr = collect_static(bdf)
        curr_fp = fingerprint(curr)
        base = baseline.get(bdf)
        if base is None:
            new_baseline[bdf] = {
                "first_seen_ts": int(time.time()),
                "attrs": curr,
                "fingerprint": curr_fp,
            }
            verdict = {"verdict": "first_seen",
                        "reason": "Baseline recorded.",
                        "severity": "info"}
            diffs: list = []
        else:
            diffs = diff_attrs(base.get("attrs", {}), curr)
            verdict = classify(diffs)
        if rank.get(verdict["severity"], 0) > rank.get(worst_severity, 0):
            worst_severity = verdict["severity"]
        per_card.append({
            "bdf": bdf,
            "vendor_device": f"{curr.get('vendor')}:{curr.get('device')}",
            "subsystem": (f"{curr.get('subsystem_vendor')}:"
                           f"{curr.get('subsystem_device')}"),
            "irq": curr.get("irq"),
            "fingerprint": curr_fp,
            "drift": diffs,
            "verdict": verdict,
        })
    if new_baseline != baseline:
        save_baseline(new_baseline)
    return {
        "ok": True,
        "cards": per_card,
        "card_count": len(per_card),
        "worst_severity": worst_severity,
    }
