"""Module nvme_iosched — NVMe I/O scheduler + readahead tuner (R&D #30.3).

A 32 GiB GGUF loaded with mmap from NVMe spends 5-15 s warming the
page cache on first inference. On modern NVMe, the `none` (no-op)
scheduler + `read_ahead_kb=4096` cuts that nearly in half — but
Ubuntu (and Debian-based distros) ship `mq-deadline` + 128 KiB
readahead by default, leaving real performance on the table.

Three sysfs reads per NVMe device:

  /sys/block/<dev>/queue/scheduler        bracketed-active variant
  /sys/block/<dev>/queue/read_ahead_kb    in KiB
  /sys/block/<dev>/queue/nr_requests      tag depth (informational)

Verdicts:
  optimal                 scheduler=none AND read_ahead_kb >= 1024
  suboptimal_scheduler    scheduler != none, readahead fine
  low_readahead           scheduler=none, readahead < 1024
  both_bad                both wrong (Ubuntu default state)
  unknown                 unreadable

Recommendation includes both an immediate `echo > sysfs` line and a
persistent udev rule (sysfs values reset across reboots) — copy-paste,
no daemon, no sudo at audit time.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "nvme_iosched"


_SYS_BLOCK = "/sys/block"


_BRACKET_RE = re.compile(r"\[([^\]]+)\]")


def list_nvme_devices(root: str = _SYS_BLOCK) -> list[str]:
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    return [n for n in names if n.startswith("nvme")
            and os.path.isdir(os.path.join(root, n))]


def read_attr(root: str, dev: str, attr: str) -> Optional[str]:
    p = os.path.join(root, dev, "queue", attr)
    try:
        with open(p) as f:
            return f.read().strip()
    except (OSError, IOError):
        return None


def parse_scheduler(s: Optional[str]) -> Optional[str]:
    """Active scheduler is the bracketed token in lines like
    `[none] mq-deadline kyber`."""
    if not s:
        return None
    m = _BRACKET_RE.search(s)
    return m.group(1) if m else None


def _as_int(s: Optional[str]) -> Optional[int]:
    if s is None:
        return None
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


_MIN_READAHEAD_KIB = 1024
_RECOMMENDED_READAHEAD_KIB = 4096


def classify(attrs: dict, dev: str = "nvme0n1") -> dict:
    sched = attrs.get("scheduler")
    ra = attrs.get("read_ahead_kb")
    if sched is None or ra is None:
        return {
            "verdict": "unknown",
            "reason": ("Could not read scheduler or read_ahead_kb — "
                       "unusual sysfs layout."),
            "recommendation": "",
        }
    sched_bad = sched != "none"
    ra_bad = ra < _MIN_READAHEAD_KIB
    rec_lines: list = []
    if sched_bad:
        rec_lines.append(
            f"echo none | sudo tee /sys/block/{dev}/queue/scheduler"
        )
    if ra_bad:
        rec_lines.append(
            f"echo {_RECOMMENDED_READAHEAD_KIB} | sudo tee "
            f"/sys/block/{dev}/queue/read_ahead_kb"
        )
    if rec_lines:
        rec_lines.append(
            "# persistent: /etc/udev/rules.d/60-nvme-mmap.rules"
        )
        rec_lines.append(
            'ACTION=="add|change", KERNEL=="nvme[0-9]*n[0-9]*", '
            'ATTR{queue/scheduler}="none", '
            f'ATTR{{queue/read_ahead_kb}}="{_RECOMMENDED_READAHEAD_KIB}"'
        )
    rec = "\n".join(rec_lines)
    if sched_bad and ra_bad:
        return {
            "verdict": "both_bad",
            "reason": (f"scheduler={sched} (Ubuntu default mq-deadline "
                       f"adds queue latency on NVMe) AND read_ahead_kb="
                       f"{ra} KiB (too low for mmap'd GGUF). Cold-load "
                       f"a 32 GiB model is ~2x slower than it needs to be."),
            "recommendation": rec,
        }
    if sched_bad:
        return {
            "verdict": "suboptimal_scheduler",
            "reason": (f"scheduler={sched} — on NVMe the device handles "
                       f"its own request reordering, so any kernel "
                       f"scheduler other than `none` adds latency for "
                       f"no benefit."),
            "recommendation": rec,
        }
    if ra_bad:
        return {
            "verdict": "low_readahead",
            "reason": (f"read_ahead_kb={ra} KiB is too low for mmap-loaded "
                       f"GGUF/safetensors files; the kernel issues many "
                       f"small reads instead of a few big ones."),
            "recommendation": rec,
        }
    return {
        "verdict": "optimal",
        "reason": (f"scheduler=none + read_ahead_kb={ra} KiB — ideal for "
                   f"mmap'd LLM weights."),
        "recommendation": "",
    }


_RANK = {
    "optimal": 0,
    "unknown": 1,
    "low_readahead": 2,
    "suboptimal_scheduler": 3,
    "both_bad": 4,
}


def status(cfg=None) -> dict:
    devs = list_nvme_devices(_SYS_BLOCK)
    cards: list = []
    worst = "optimal"
    for dev in devs:
        raw_sched = read_attr(_SYS_BLOCK, dev, "scheduler")
        sched = parse_scheduler(raw_sched)
        ra = _as_int(read_attr(_SYS_BLOCK, dev, "read_ahead_kb"))
        nr = _as_int(read_attr(_SYS_BLOCK, dev, "nr_requests"))
        verdict = classify(
            {"scheduler": sched, "read_ahead_kb": ra, "nr_requests": nr},
            dev=dev,
        )
        if _RANK.get(verdict["verdict"], 0) > _RANK.get(worst, 0):
            worst = verdict["verdict"]
        cards.append({
            "device": dev,
            "scheduler": sched,
            "scheduler_raw": raw_sched,
            "read_ahead_kb": ra,
            "nr_requests": nr,
            "verdict": verdict,
        })
    if not cards:
        return {"ok": True, "device_count": 0,
                "devices": [], "worst_verdict": "no_nvme"}
    return {"ok": True, "device_count": len(cards),
            "devices": cards, "worst_verdict": worst}
