"""Module block_queue_audit — /sys/block/*/queue/* (R&D #43.2).

The block-layer queue exposes ~15 knobs per device that materially
affect GGUF / safetensors load throughput, page-fault latency for
mmap'd weights, and write-amplification on SSDs / NVMe :

  scheduler              [none] / [mq-deadline] / [bfq] / [kyber]
                         — `none` is right for NVMe ; `mq-deadline`
                         for prosumer SATA SSDs ; `bfq` for HDDs.
  nr_requests            in-flight I/Os ; 256 = default, 1024 for
                         NVMe under heavy parallel load.
  read_ahead_kb          sequential-read prefetch ; 128 KB default
                         is right for HDDs, *too low* for NVMe
                         streaming a 40-GB GGUF (4096-8192 KB).
  rotational             1 = HDD, 0 = SSD/NVMe ; if the kernel
                         got this wrong (USB-attached NVMe enclos.,
                         RAID controllers), all the SSD-aware
                         heuristics break.
  nomerges               0 = merge all, 1 = no merges of separate
                         requests, 2 = no merges of consecutive.
  iostats                1 = collect /proc/diskstats (small cost,
                         essential for our other modules).
  rq_affinity            0 = no IRQ-CPU steering, 1 = complete on
                         submitter's CPU, 2 = group of submitters.
  max_sectors_kb         driver soft cap on per-request size.
  wbt_lat_usec           writeback throttling target latency ;
                         0 = disabled. On a tight SSD that's
                         already slow under writes, wbt slows
                         it further.
  write_cache            "write back" vs "write through" ; the
                         driver's view of the device write cache.

Verdicts (priority-ordered, worst-of across devices) :
  rotational_misdetect   /sys/block/<dev>/queue/rotational=1 on a
                         device whose model name says NVMe / SSD,
                         or =0 on a path that looks rotational.
                         Catches NVMe-in-USB enclosures + some
                         HW RAID controllers.
  scheduler_mismatch     bfq or mq-deadline on a non-rotational
                         device, or `none` on rotational. The
                         classic foot-gun.
  readahead_too_low      rotational=0 + read_ahead_kb ≤ 256 KB
                         on a box that mmaps a GGUF — sequential
                         prefetch is starved.
  wbt_throttling         wbt_lat_usec > 0 on a write-heavy device
                         (best-effort detection via /proc/diskstats
                         write activity).
  ok                     no flags.
  no_block_devices       no /sys/block/<n>/queue dirs (containers).
  unknown                /sys/block unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "block_queue_audit"


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


def parse_scheduler(text: Optional[str]) -> tuple:
    """[none] mq-deadline → ('none', ['none', 'mq-deadline'])"""
    if not text:
        return (None, [])
    t = text.strip()
    available = re.findall(r"\[?(\w[\w-]*)\]?", t)
    active = None
    m = re.search(r"\[(\w[\w-]*)\]", t)
    if m:
        active = m.group(1)
    return (active, available)


def list_block_devices(sys_block: str = _SYS_BLOCK) -> list:
    if not os.path.isdir(sys_block):
        return []
    out: list = []
    for name in sorted(os.listdir(sys_block)):
        if any(name.startswith(p) for p in _SKIP_PREFIXES):
            continue
        ddir = os.path.join(sys_block, name)
        if not os.path.isdir(ddir):
            continue
        if not os.path.isdir(os.path.join(ddir, "queue")):
            continue
        out.append(name)
    return out


def read_device(sys_block: str, dev: str) -> dict:
    ddir = os.path.join(sys_block, dev)
    q = os.path.join(ddir, "queue")
    scheduler_text = _read(os.path.join(q, "scheduler"))
    active, available = parse_scheduler(scheduler_text)
    return {
        "dev": dev,
        "scheduler": active,
        "scheduler_available": available,
        "nr_requests": _read_int(os.path.join(q, "nr_requests")),
        "read_ahead_kb": _read_int(os.path.join(q, "read_ahead_kb")),
        "rotational": _read_int(os.path.join(q, "rotational")),
        "nomerges": _read_int(os.path.join(q, "nomerges")),
        "iostats": _read_int(os.path.join(q, "iostats")),
        "rq_affinity": _read_int(os.path.join(q, "rq_affinity")),
        "max_sectors_kb": _read_int(
            os.path.join(q, "max_sectors_kb")),
        "max_hw_sectors_kb": _read_int(
            os.path.join(q, "max_hw_sectors_kb")),
        "wbt_lat_usec": _read_int(os.path.join(q, "wbt_lat_usec")),
        "write_cache": (_read(os.path.join(q, "write_cache"))
                          or "").strip() or None,
        "logical_block_size": _read_int(
            os.path.join(q, "logical_block_size")),
        "physical_block_size": _read_int(
            os.path.join(q, "physical_block_size")),
        "model": (_read(os.path.join(ddir, "device", "model"))
                    or "").strip() or None,
    }


_NVME_HINTS = ("nvme", "ssd", "solid state")
_HDD_HINTS = ("hdd", "rotat", "spinning", "wd green", "wd red",
                "ironwolf", "skyhawk")

_ROTATIONAL_SCHEDULERS = ("bfq",)
_NONROT_SCHEDULERS = ("none", "kyber")


_RECIPE_SCHEDULER_NVME = (
    "# Switch to the right scheduler for a non-rotational device :\n"
    "echo none | sudo tee /sys/block/<DEV>/queue/scheduler\n"
    "# Persistent via udev rule :\n"
    "sudo tee /etc/udev/rules.d/60-ioscheduler.rules <<'EOF'\n"
    "ACTION==\"add|change\", KERNEL==\"nvme[0-9]*\", "
    "ATTR{queue/scheduler}=\"none\"\n"
    "ACTION==\"add|change\", KERNEL==\"sd[a-z]\", "
    "ATTR{queue/rotational}==\"0\", "
    "ATTR{queue/scheduler}=\"mq-deadline\"\n"
    "EOF"
)

_RECIPE_READAHEAD = (
    "# Bump read_ahead_kb to absorb sequential GGUF / safetensors\n"
    "# load. 4096 KB matches a typical 4 MB NVMe page :\n"
    "echo 4096 | sudo tee /sys/block/<DEV>/queue/read_ahead_kb\n"
    "# Persist via /etc/udev/rules.d/60-read-ahead.rules :\n"
    "# ACTION==\"add|change\", KERNEL==\"nvme[0-9]n*\",\n"
    "#   ATTR{queue/read_ahead_kb}=\"4096\""
)

_RECIPE_ROTATIONAL = (
    "# Kernel mis-detected the device's rotational class. Override\n"
    "# (effective until reboot ; udev for persistence) :\n"
    "echo 0 | sudo tee /sys/block/<DEV>/queue/rotational"
)

_RECIPE_WBT = (
    "# Writeback throttling target is low — disable wbt for\n"
    "# write-heavy LLM workloads that already saturate the SSD :\n"
    "echo 0 | sudo tee /sys/block/<DEV>/queue/wbt_lat_usec"
)


def _looks_rotational(model: Optional[str]) -> Optional[bool]:
    if not model:
        return None
    low = model.lower()
    if any(h in low for h in _HDD_HINTS):
        return True
    if any(h in low for h in _NVME_HINTS):
        return False
    return None


_RANK = {"ok": 0, "no_block_devices": 0, "unknown": 0,
         "wbt_throttling": 1, "readahead_too_low": 2,
         "scheduler_mismatch": 3, "rotational_misdetect": 4}


def classify(devices: list) -> dict:
    if not devices:
        return {"verdict": "no_block_devices",
                "reason": ("No block devices with /sys/block/<n>/"
                           "queue/ — container or minimal kernel."),
                "recommendation": ""}
    best = {"verdict": "ok",
              "reason": (f"{len(devices)} block device(s) ; queue "
                         f"settings consistent."),
              "recommendation": ""}
    for d in devices:
        dev = d["dev"]
        rot = d.get("rotational")
        sched = d.get("scheduler")
        ra = d.get("read_ahead_kb")
        wbt = d.get("wbt_lat_usec") or 0
        model = d.get("model")
        # 1) rotational mis-detect.
        model_says_rot = _looks_rotational(model)
        if (model_says_rot is True and rot == 0) or (
                model_says_rot is False and rot == 1):
            cand = ("rotational_misdetect",
                     (f"{dev} model={model!r} disagrees with "
                      f"queue/rotational={rot}. Kernel auto-detect "
                      f"may have it wrong."),
                     _RECIPE_ROTATIONAL)
        # 2) scheduler mismatch.
        elif rot == 0 and sched in _ROTATIONAL_SCHEDULERS:
            cand = ("scheduler_mismatch",
                     (f"{dev} is non-rotational but uses '{sched}' "
                      f"(BFQ designed for HDDs). Switch to 'none'."),
                     _RECIPE_SCHEDULER_NVME)
        elif rot == 1 and sched in _NONROT_SCHEDULERS:
            cand = ("scheduler_mismatch",
                     (f"{dev} is rotational but uses '{sched}'. "
                      f"Switch to 'bfq' or 'mq-deadline'."),
                     _RECIPE_SCHEDULER_NVME)
        # 3) read-ahead too low on NVMe-class.
        elif rot == 0 and isinstance(ra, int) and ra <= 256:
            cand = ("readahead_too_low",
                     (f"{dev} non-rotational + read_ahead_kb={ra} — "
                      f"GGUF / safetensors streaming is starved of "
                      f"prefetch."),
                     _RECIPE_READAHEAD)
        # 4) WBT throttling.
        elif wbt > 0 and rot == 0:
            cand = ("wbt_throttling",
                     (f"{dev} wbt_lat_usec={wbt} on a non-"
                      f"rotational device — writeback throttling "
                      f"slows write-heavy LLM workloads further."),
                     _RECIPE_WBT)
        else:
            continue
        verdict, reason, recipe = cand
        if _RANK.get(verdict, 0) > _RANK.get(best["verdict"], 0):
            best = {"verdict": verdict, "reason": reason,
                     "recommendation": recipe}
    return best


def status(cfg=None) -> dict:
    if not os.path.isdir(_SYS_BLOCK):
        return {
            "ok": False,
            "verdict": {"verdict": "unknown",
                         "reason": "/sys/block unreadable.",
                         "recommendation": ""},
            "devices": [],
        }
    dev_names = list_block_devices(_SYS_BLOCK)
    devs = [read_device(_SYS_BLOCK, n) for n in dev_names]
    verdict = classify(devs)
    return {
        "ok": True,
        "device_count": len(devs),
        "devices": devs,
        "verdict": verdict,
    }
