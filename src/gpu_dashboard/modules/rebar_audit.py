"""Module rebar_audit — Resizable-BAR (ReBAR) auditor (R&D #27.1).

NVIDIA Ampere+ cards support Resizable BAR (ReBAR), which lets the
CPU map the *entire* GPU VRAM into the host address space instead
of a 256 MiB sliding window. Enabling ReBAR via UEFI gives 5-12 %
free inference perf (large weight tensor transfers stop bouncing
through the small aperture).

Most desktop owners with a recent BIOS update have it off because :
  - UEFI default is "Above 4G Decoding = enabled, ReBAR = disabled"
  - The toggle is buried in advanced BIOS menus
  - No Linux warning when it's off

This module reads /sys/bus/pci/devices/<bdf>/resource — each line
is a BAR (start end flags). BAR1 (the framebuffer aperture) size
is `end - start + 1`. Compare it against memory.total :

  - BAR1 ≥ 80% of total VRAM   → rebar_on (5-12% perf headroom captured)
  - BAR1 < 80%                 → rebar_off (enable in UEFI)
  - cannot read BAR1           → unknown

Pure sysfs + one nvidia-smi call. No sudo.

stdlib only.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Optional


NAME = "rebar_audit"


_PCI_ROOT = "/sys/bus/pci/devices"


def list_nvidia_bdfs(sys_root: str = _PCI_ROOT) -> list[str]:
    """Return BDFs of NVIDIA PCI devices."""
    out: list[str] = []
    try:
        for name in sorted(os.listdir(sys_root)):
            vp = os.path.join(sys_root, name, "vendor")
            try:
                with open(vp) as f:
                    if f.read().strip().lower() == "0x10de":
                        out.append(name)
            except OSError:
                continue
    except OSError:
        return []
    return out


def parse_bar_size(start_hex: str, end_hex: str) -> Optional[int]:
    """BAR size in bytes from (start, end) hex strings. Returns None if
    the BAR is unused (start == end == 0)."""
    try:
        start = int(start_hex, 16)
        end = int(end_hex, 16)
    except ValueError:
        return None
    if start == 0 and end == 0:
        return None
    return end - start + 1


def read_bars(bdf: str, sys_root: str = _PCI_ROOT) -> list[Optional[int]]:
    """Parse /sys/bus/pci/devices/<bdf>/resource into list of BAR sizes
    (or None for unused BARs)."""
    p = os.path.join(sys_root, bdf, "resource")
    try:
        with open(p) as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    bars: list = []
    for line in lines:
        parts = line.split()
        if len(parts) < 2:
            bars.append(None)
            continue
        bars.append(parse_bar_size(parts[0], parts[1]))
    return bars


def gpu_memory_total_bytes(timeout: float = 2.0) -> dict[str, int]:
    """nvidia-smi --query-gpu=pci.bus_id,memory.total → {bdf: bytes}."""
    if not shutil.which("nvidia-smi"):
        return {}
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=pci.bus_id,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return {}
    if r.returncode != 0:
        return {}
    out: dict[str, int] = {}
    for line in r.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        bdf_raw = parts[0].lower()
        # nvidia-smi gives 00000000:01:00.0 ; sysfs uses 0000:01:00.0
        bdf = bdf_raw.lstrip("0").lstrip(":")
        bdf = "0000:" + bdf if not bdf.startswith("0000:") and ":" in bdf else bdf
        # Easier: normalize both sides downstream
        try:
            mib = int(parts[1])
            out[bdf_raw] = mib * 1024 * 1024
        except ValueError:
            continue
    return out


def _normalize_bdf(bdf: str) -> str:
    """Normalize PCI BDF strings to '0000:bb:dd.f' form."""
    s = bdf.lower()
    # nvidia-smi gives '00000000:01:00.0' (8-digit domain)
    parts = s.split(":")
    if len(parts) == 3 and len(parts[0]) > 4:
        parts[0] = parts[0][-4:]
    return ":".join(parts)


def classify(bar1_size: Optional[int],
              total_vram_bytes: Optional[int]) -> dict:
    """Return {verdict, reason, recommendation, bar1_pct_of_vram}."""
    if bar1_size is None or total_vram_bytes is None or total_vram_bytes == 0:
        return {"verdict": "unknown",
                "reason": ("Could not determine BAR1 size or total VRAM. "
                           "Card may be off-bus."),
                "recommendation": "",
                "bar1_pct_of_vram": None}
    pct = (bar1_size / total_vram_bytes) * 100
    if pct >= 80:
        return {"verdict": "rebar_on",
                "reason": (f"BAR1 covers {pct:.0f}% of VRAM "
                           f"({bar1_size / 1024 ** 3:.1f} GiB). "
                           "ReBAR is enabled — full address-space mapping."),
                "recommendation": "",
                "bar1_pct_of_vram": round(pct, 1)}
    if pct < 5:
        return {"verdict": "rebar_off",
                "reason": (f"BAR1 is only {bar1_size / 1024 ** 2:.0f} MiB "
                           f"({pct:.1f}% of VRAM). Legacy sliding-window "
                           "aperture — enable ReBAR in UEFI for 5-12% perf."),
                "recommendation": ("Reboot to UEFI → Advanced → PCI Subsystem → "
                                    "set 'Above 4G Decoding' AND "
                                    "'Resizable BAR Support' to Enabled."),
                "bar1_pct_of_vram": round(pct, 1)}
    return {"verdict": "partial",
            "reason": (f"BAR1 is {pct:.0f}% of VRAM — partial ReBAR support. "
                       "Some perf headroom still on the table."),
            "recommendation": "Verify UEFI Above-4G + ReBAR are both enabled.",
            "bar1_pct_of_vram": round(pct, 1)}


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    bdfs = list_nvidia_bdfs()
    memory_map_raw = gpu_memory_total_bytes()
    # Normalize keys
    memory_map = {_normalize_bdf(k): v for k, v in memory_map_raw.items()}
    cards: list = []
    worst_verdict = "rebar_on"
    rank = {"rebar_on": 0, "partial": 1, "rebar_off": 2, "unknown": 3}
    for bdf in bdfs:
        bars = read_bars(bdf)
        bar1 = bars[1] if len(bars) > 1 else None
        norm_bdf = _normalize_bdf(bdf)
        total = memory_map.get(norm_bdf)
        verdict = classify(bar1, total)
        if rank.get(verdict["verdict"], 0) > rank.get(worst_verdict, 0):
            worst_verdict = verdict["verdict"]
        cards.append({
            "bdf": bdf,
            "bar1_bytes": bar1,
            "bar1_mib": (round(bar1 / 1024 ** 2, 1)
                          if bar1 is not None else None),
            "total_vram_bytes": total,
            "total_vram_gib": (round(total / 1024 ** 3, 1)
                                if total is not None else None),
            "verdict": verdict,
        })
    return {
        "ok": True,
        "cards": cards,
        "card_count": len(cards),
        "worst_verdict": worst_verdict,
    }
