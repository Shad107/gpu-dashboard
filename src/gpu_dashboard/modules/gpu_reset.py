"""Module gpu_reset — GPU reset counter / RMA-candidate detector (R&D #22.1).

Closes the loop on shipped XID (#7) + GSP-RM monitors (#21.3). When
the kernel driver auto-recovers from a hard fault — GPU fallen off
bus, channel hang, GR exception — it bumps an internal counter and
re-initializes the device. Inference resumes "fine" so the user
never notices. But three resets per week on the same card means a
hardware-defect candidate that should be RMA'd.

This module reports :

  - per-GPU reset count from /sys/class/drm/card*/device/reset_count
    (Linux 5.7+ kernel DRM core)
  - kernel-log scan for "GPU has been reset" / "GPU has fallen off
    the bus" lines in the last 7 days
  - verdict :
      clean       (0 resets, no log signals)
      occasional  (1-2 resets in a week)
      frequent    (3-9 — investigate)
      rma         (≥10 — card likely defective)

Baselines the reset count per GPU on first observation so subsequent
resets are deltas from when monitoring started.

stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from typing import Optional


NAME = "gpu_reset"


_BASELINE_PATH = "~/.config/gpu-dashboard/gpu_reset_baseline.json"


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


def list_drm_cards(drm_root: str = "/sys/class/drm") -> list[str]:
    """List /sys/class/drm/cardN devices (skip render nodes)."""
    out: list[str] = []
    try:
        for name in sorted(os.listdir(drm_root)):
            if re.fullmatch(r"card\d+", name):
                out.append(os.path.join(drm_root, name))
    except OSError:
        return []
    return out


def read_reset_count(card_path: str) -> Optional[int]:
    """Return reset_count for /sys/class/drm/cardN, or None if missing."""
    p = os.path.join(card_path, "device", "reset_count")
    try:
        with open(p) as f:
            txt = f.read().strip()
        return int(txt) if txt.lstrip("-").isdigit() else None
    except OSError:
        return None


def read_bdf(card_path: str) -> Optional[str]:
    """uevent has PCI_SLOT_NAME. Or readlink device → bdf."""
    try:
        link = os.readlink(os.path.join(card_path, "device"))
        bdf = link.rsplit("/", 1)[-1]
        # Format 0000:01:00.0
        if re.match(r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.\d$", bdf):
            return bdf
    except OSError:
        pass
    return None


def is_nvidia_card(card_path: str) -> bool:
    """Check vendor id in /sys/class/drm/cardN/device/vendor."""
    p = os.path.join(card_path, "device", "vendor")
    try:
        with open(p) as f:
            return f.read().strip().lower() == "0x10de"
    except OSError:
        return False


_RESET_PATTERNS = [
    (re.compile(r"NVRM:.*GPU.*has been reset", re.IGNORECASE), "reset"),
    (re.compile(r"NVRM:.*has fallen off the bus", re.IGNORECASE), "fallen_off_bus"),
    (re.compile(r"NVRM:.*GR\d+ exception", re.IGNORECASE), "gr_exception"),
    (re.compile(r"NVRM:.*channel hang", re.IGNORECASE), "channel_hang"),
    (re.compile(r"NVRM:.*GPU recovery", re.IGNORECASE), "recovery"),
]


def journalctl_kernel(since: str = "7 days ago",
                       timeout: float = 4.0) -> Optional[list[str]]:
    if not shutil.which("journalctl"):
        return None
    try:
        r = subprocess.run(
            ["journalctl", "-k", f"--since={since}", "--no-pager",
             "--output=cat"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    return r.stdout.splitlines() if r.returncode == 0 else None


def scan_kernel_for_resets(lines: list[str]) -> list[dict]:
    """Match patterns against kernel log lines."""
    out: list[dict] = []
    for ln in lines:
        if "NVRM" not in ln and "nvidia" not in ln.lower():
            continue
        for pat, kind in _RESET_PATTERNS:
            if pat.search(ln):
                out.append({"kind": kind, "line": ln.strip()[:240]})
                break
    return out


def classify(delta_resets: int, log_event_count: int) -> dict:
    """Combine reset-count delta with kernel-log signal count. Returns
    {verdict, reason, recommendation}."""
    total = delta_resets + log_event_count
    if total == 0:
        return {"verdict": "clean",
                "reason": "No resets recorded since baseline.",
                "recommendation": ""}
    if total <= 2:
        return {"verdict": "occasional",
                "reason": (f"{total} reset event(s) in last 7 days. "
                           "Single-event causes are usually transient."),
                "recommendation": ""}
    if total < 10:
        return {"verdict": "frequent",
                "reason": (f"{total} reset event(s) in last 7 days. "
                           "Check power supply, PCIe seating, and dmesg "
                           "for the original XID code."),
                "recommendation": "journalctl -k | grep -E 'NVRM|XID'"}
    return {"verdict": "rma",
            "reason": (f"{total} reset event(s) in last 7 days. "
                       "Card is likely defective — start an RMA before "
                       "your warranty window closes."),
            "recommendation": "Document with nvidia-bug-report.sh before RMA."}


def update_baseline_and_get_delta(reset_counts: dict[str, int]) -> dict[str, int]:
    """Persist first-seen counts as baseline ; return delta per BDF."""
    baseline = load_baseline()
    deltas: dict[str, int] = {}
    changed = False
    for bdf, count in reset_counts.items():
        if bdf not in baseline:
            baseline[bdf] = {"first_seen_count": count,
                              "first_seen_ts": int(time.time())}
            changed = True
            deltas[bdf] = 0
        else:
            deltas[bdf] = max(0, count - baseline[bdf]["first_seen_count"])
    if changed:
        save_baseline(baseline)
    return deltas


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    cards = list_drm_cards()
    nvidia_cards = [c for c in cards if is_nvidia_card(c)]
    reset_counts: dict[str, int] = {}
    per_card: list[dict] = []
    for c in nvidia_cards:
        bdf = read_bdf(c) or os.path.basename(c)
        rc = read_reset_count(c)
        per_card.append({
            "card": os.path.basename(c),
            "bdf": bdf,
            "reset_count": rc,
        })
        if rc is not None:
            reset_counts[bdf] = rc
    deltas = update_baseline_and_get_delta(reset_counts)
    kernel_lines = journalctl_kernel() or []
    events = scan_kernel_for_resets(kernel_lines)
    total_delta = sum(deltas.values()) if deltas else 0
    verdict = classify(total_delta, len(events))
    return {
        "ok": True,
        "cards": [{**c, "delta_resets": deltas.get(c["bdf"], 0)}
                  for c in per_card],
        "card_count": len(per_card),
        "kernel_events": events[-20:],
        "kernel_event_count": len(events),
        "total_delta_resets": total_delta,
        "verdict": verdict,
    }
